#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""적재가 온전한가. **네트워크를 타지 않는다.**

`verify.py`(법제처가 바뀌었나) · `pytest`(우리 코드가 회귀했나) 와 함께 진단 삼각형을 이룬다. 셋이 각각 다른 질문에 답하므로, 수집이 빈손으로 올 때 어느 쪽이 깨졌는지가 갈린다.

**게이트와 보고값을 반드시 가른다.** 원천이 보장하지 않는 것을 게이트로 걸면 고칠 수 없는 빨간불이 생기고, 고칠 수 없는 빨간불은 표 전체를 무시하게 만든다. 헌재 의율 커버리지가 그 예다 — 원천 필드가 그만큼만 채워지므로 게이트로 걸면 영원히 빨갛다.
"""

from __future__ import annotations


import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from law import schema as 스키마
from law import conn as 연결
from law import store as 저장
from law import normalize as 정규화  # noqa: E402


def _날짜위반SQL(상세: bool = False) -> str:
    """`적재.날짜컬럼` 전부에 같은 술어를 건다. **목록을 여기 다시 적지 않는다** —
    새 날짜 컬럼이 생긴 날 감사만 조용히 빠지는 것을 막는 유일한 방법이다."""
    조각 = [
        f"SELECT '{t}.{c}' 자리, COUNT(*) n FROM {t}"
        f" WHERE {정규화.날짜위반술어.format(c=c)}"
        for t, c in 정규화.날짜컬럼
    ]
    합 = " UNION ALL ".join(조각)
    if 상세:
        return (f"SELECT COALESCE(GROUP_CONCAT(자리||' '||n, ' · '), '없음')"
                f" FROM ({합}) WHERE n > 0")
    return f"SELECT COALESCE(SUM(n), 0) FROM ({합})"


드리프트질의 = 스키마.드리프트질의  # 정의는 SCHEMA 를 소유한 모듈에 있다


# ── 직전 정상과 견주는 축 ────────────────────────────────────────────────────
#
# 아래 게이트는 전부 **현재 DB 안에서** 등식을 본다. 그래서 행이 사라지면 등식의 양변이
# 함께 사라져 오히려 잘 맞는다 — 실측(2026-08-28)으로 빈 DB 에서 23개 중 21개가 초록이었다.
# **게이트를 더 다는 것은 이 성질을 안 바꾼다.** 축을 하나 넣는다: *감사는 직전 정상과
# 비교한다.* 그리고 그 직전 정상은 **DB 밖에** 있어야 한다 — 안에 두면 DB 를 통째로
# 갈아치운 사고에서 기준선도 같이 사라진다. 정책(무엇이 급감인가)은 세대를 아는 쪽이
# 소유한다; 여기 숫자를 복사해 두면 두 벌이 갈린다.

_코퍼스이름 = "law"
_직전캐시: dict[int, tuple] = {}


def _백업모듈():
    """`backup.py` 를 **파일 하나만 이름 붙여** 불러온다.

    ⚠️ **레포 루트를 `sys.path` 에 올리지 마라** — 네 스킬의 `Scripts/` 에 같은 이름이
    여럿 있어, 먼저 import 된 쪽이 다른 스킬을 조용히 깨뜨린다(테스트에서 실측했다).
    """
    if "backup" in sys.modules:
        return sys.modules["backup"]
    경로 = 연결.SKILL_DIR / "Scripts" / "backup.py"
    if not 경로.is_file():
        return None
    명세 = importlib.util.spec_from_file_location("backup", 경로)
    sys.modules["backup"] = m = importlib.util.module_from_spec(명세)
    명세.loader.exec_module(m)
    return m


def _직전정상(conn) -> tuple[str | None, list]:
    """`(견준 세대 이름 | None, 급감한 표들)`. 세대가 없으면 `(None, [])`."""
    if (있는것 := _직전캐시.get(id(conn))) is not None:
        return 있는것
    답: tuple[str | None, list] = (None, [])
    if (백업 := _백업모듈()) and (기준 := 백업.기준선(_코퍼스이름)):
        이름, 전 = 기준
        지금 = {}
        for t in 전:
            try:
                지금[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                continue      # 표가 없다 — A0 가 진다
        답 = (이름, 백업.급감표(_코퍼스이름, 전, 지금))
    _직전캐시[id(conn)] = 답
    return 답


def _급감수(conn) -> int:
    # ⚠️ **원장은 세지 않는다.** 일이 끝나면 줄어드는 표라 급감으로 세면 가장 잘 돌아간
    #    날이 가장 빨갛다. 그래도 눈에서 없애지는 않는다 — 상세는 아래가 전부 낸다.
    return sum(1 for *_, 원장 in _직전정상(conn)[1] if not 원장)


def _급감상세(conn) -> str:
    이름, 난것 = _직전정상(conn)
    if 이름 is None:
        # ⚠️ **"기준선 없음" 과 "견줘 봤는데 0" 을 같은 얼굴로 내면 안 된다.** 백업이
        #    멈춘 날 이 축이 조용히 아무것도 안 지키는데 게이트는 초록이다.
        return "기준선 없음 — 검증된 백업 세대가 아직 없다"
    if not 난것:
        return f"{이름} 대비 급감 없음"
    return f"{이름} 대비 " + " · ".join(
        f"{t} {옛:,}→{새:,} ({(1 - 새 / 옛) * 100:.1f}% 감소)"
        + (" (원장 — 판정 아님)" if 원장 else "")
        for t, 옛, 새, 원장 in 난것
    )


def _뷰이름들(schema: str) -> list[str]:
    """`SCHEMA` 가 만드는 뷰 이름 전부. **목록을 손으로 적지 않는 이유가 이것이다** —
    뷰를 하나 더한 날 그 목록만 낡으면 새 뷰는 아무도 안 지킨다.
    """
    return re.findall(r"CREATE VIEW (\S+) AS", schema)


# ⚠️ **빈 것도 위반이다 — 뷰는 사는데 필터가 전부를 걸러내는 상태가 실재하고, 그러면
#    "현행 법이 하나도 없다"가 조용히 0건으로 나온다.** 다만 아래 셋은 **앞으로 시행될
#    개정이 하나도 없는 날** 정상적으로 빈다. 그런 뷰까지 세면 흔한 날에 빨개지고,
#    늑대를 부르는 게이트는 곧 무시된다. 예외에는 반드시 이유가 붙는다.
비어도되는뷰 = {
    "시행대기법령": "오늘 효력이면서 본문이 미래 렌더링인 법이 없는 날",
    "시행예정법령": "공포됐지만 아직 시행 전인 법이 없는 날",
    "조문개정비교": "현행 × 시행예정 쌍이 없는 날",
}


def _깨진뷰(conn) -> int:
    return len(_깨진뷰상세(conn)[1])


def _깨진뷰상세(conn) -> tuple[int, list[str]]:
    """`(뷰 수, 문제가 있는 뷰들)`. **조회해 보는 것 말고 확인할 방법이 없다** —
    뷰가 가리키는 컬럼이 사라져도 `PRAGMA integrity_check` 는 통과하고 `sqlite_master`
    에도 멀쩡히 앉아 있다.
    """
    이름들 = _뷰이름들(스키마.SCHEMA)
    난것 = []
    for v in 이름들:
        try:
            빈가 = conn.execute(f'SELECT COUNT(*) FROM "{v}"').fetchone()[0] == 0
        except sqlite3.Error as e:
            난것.append(f"{v}({e})")
            continue
        if 빈가 and v not in 비어도되는뷰:
            난것.append(f"{v}(비었다)")
    return len(이름들), 난것


게이트: list[tuple[str, str, str]] = [
    (
        "A0",
        "상수와 DB 가 갈린 테이블 수",
        # ⚠️ **주석이 곧 문서인 DB 에서 이 검사가 없으면 끊어진 고리가 조용하다.**
        #    상수→DB 갱신은 `collect.py` 가 실행 중 `migrate()` 를 부르는 것이 전부인데,
        #    원천 진단이 빨가면 수집을 건너뛰어 `migrate()` 도 안 돈다. 그러면 주석을
        #    고쳐 놓고 `.schema` 는 옛 것을 내놓는데 **말해 주는 것이 하나도 없다.**
        드리프트질의,
    ),
    # ⚠️ **빈 DB 가 게이트를 전부 통과하면 안 된다.** 실제로 그랬다 — 모든 게이트가
    #    "잘못된 행이 없는가"만 물어서, 행이 하나도 없으면 전부 0 이 나와 초록이었다.
    #    수집이 통째로 실패한 날 초록불이 뜨는 것이 이 DB 에서 가장 나쁜 결과다.
    #    A1 이 그걸 막는다 — **목록에서 센 것과 실제로 들어온 것을 대조**한다.
    #    `본문행 + '없음' 확정 = 마지막으로 검증한 목록 건수` 여야 한다.
    # ⚠️ **`계류` 를 빼먹으면 이 게이트가 정리 가드와 싸운다.** 목록에서 빠진 것을 연속
    #    N회를 채울 때까지 안 지우는 것이 설계인데, 그동안 본문에는 그만큼이 더 있다.
    #    빼먹으면 정상 동작이 매번 빨간불로 나오고, **고칠 수 없는 빨간불은 표 전체를
    #    무시하게 만든다.**
    ("A1", "적재 건수가 마지막 목록 건수와 안 맞는 자료종류",
     "SELECT COUNT(*) FROM ("
     "  SELECT s.자료종류, s.건수 목록,"
     "    (SELECT COUNT(*) FROM 판례) * (s.자료종류='판례')"
     "  + (SELECT COUNT(*) FROM 헌재결정례) * (s.자료종류='헌재결정례')"
     "  + (SELECT COUNT(*) FROM 행정심판례) * (s.자료종류='행정심판례')"
     "  + (SELECT COUNT(*) FROM 법령해석례) * (s.자료종류='법령해석례')"
     "  + (SELECT COUNT(*) FROM 행정규칙) * (s.자료종류='행정규칙')"
     "  + (SELECT COUNT(*) FROM 법령) * (s.자료종류='법령') 본문,"
     "    (SELECT COUNT(*) FROM 수집실패 f WHERE f.자료종류=s.자료종류"
     "       AND f.실패종류='없음') 없음,"
     "    (SELECT COUNT(*) FROM 정리후보 p WHERE p.자료종류=s.자료종류) 계류,"
     # ⚠️ **철회를 안 더하면 A1 이 영원히 빨갛다.** 본문에는 있는데 마지막 목록에는 없는
     #    것이 우리가 **일부러** 남긴 행이라, 등식이 그만큼 어긋난 채로 굳는다.
     "    (SELECT COUNT(*) FROM 판례 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='판례')"
     "  + (SELECT COUNT(*) FROM 헌재결정례 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='헌재결정례')"
     "  + (SELECT COUNT(*) FROM 행정심판례 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='행정심판례')"
     "  + (SELECT COUNT(*) FROM 법령해석례 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='법령해석례')"
     "  + (SELECT COUNT(*) FROM 행정규칙 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='행정규칙')"
     "  + (SELECT COUNT(*) FROM 법령 WHERE 원천철회일 IS NOT NULL) * (s.자료종류='법령') 철회,"
     # ⚠️ **복귀했지만 본문이 아직 안 온 것은 철회 보정에서 뺀다.** 그 행은 본문에도 있고
     #    마지막 목록에도 있어 보정이 필요 없는데, 안 빼면 본문 API 가 계속 「없다」고
     #    답하는 동안 내내 빨갛다 — 우리가 고칠 수 없는 빨간불이다.
     "    COALESCE((SELECT 건수 FROM 수집상태 r WHERE r.자료종류=s.자료종류"
     "       AND r.단계='정리' AND r.키='복귀대기'), 0) 복귀대기"
     "  FROM 수집상태 s WHERE s.단계='목록' AND s.키='전체'"
     "    AND s.상태='완료' AND s.건수 > 0"
     ") WHERE 본문 + 없음 <> 목록 + 계류 + 철회 - 복귀대기"),
    # ⚠️ **A1 은 정리가 멈춘 것을 못 잡는다** — 삭제 **뒤에** 맞아떨어지는 등식이라
    #    아무것도 안 지운 날에도 초록일 수 있다. 건너뛴 사실은 여기만 안다.
    # ⚠️ **`'부분수신'` 은 여기 세지 않는다** — 네트워크가 흔들린 날마다 나는 평범한
    #    사건이라 게이트로 올리면 흔한 날에 빨개지고, 늑대를 부르는 게이트는 곧 무시된다.
    #    그건 보고값 R22 가 읽는다.
    ("A25", "정리를 건너뛴 자료종류 (원천이 이상해 삭제를 멈췄다)",
     "SELECT COUNT(*) FROM 수집상태 WHERE 단계='정리' AND 상태='건너뜀'"),
    # ⚠️ **불변식을 사람의 조심에 맡기지 않는다.** `철회기록()` 이 `수집실패` 를 치우고
    #    되물음 경로가 '없음' 을 다시 안 적지만, 둘 다 **부르는 쪽이 지켜야 하는 규약**이다.
    #    어긋나면 A1 이 같은 행을 두 번 기대해 **원인을 못 찾는 빨간불**이 된다.
    ("A26", "철회 표지와 '없음' 이 함께 있는 자료 (A1 이 같은 행을 두 번 센다)",
     " + ".join(
         f"(SELECT COUNT(*) FROM {테} t JOIN 수집실패 f"
         f" ON f.자료종류='{종}' AND f.자료ID=t.{키} AND f.실패종류='없음'"
         f" WHERE t.원천철회일 IS NOT NULL)"
         for 종, (테, 키, _) in 저장.본문테이블.items()).join(("SELECT ", "")),
    ),
    ("A1b", "목록 단계를 한 번도 완료하지 못한 자료종류 (수집이 아예 안 돌았다)",
     "SELECT 6 - COUNT(DISTINCT 자료종류) FROM 수집상태"
     " WHERE 단계='목록' AND 상태='완료' AND 건수 > 0"),
    ("A2", "본문이 없는 법령 (조문 0행)",
     "SELECT COUNT(*) FROM 법령 l WHERE NOT EXISTS"
     " (SELECT 1 FROM 조문 j WHERE j.법령일련번호=l.법령일련번호)"),
    ("A3", "수집실패 중 '없음'이 아닌 것",
     "SELECT COUNT(*) FROM 수집실패 WHERE 실패종류<>'없음'"),
    ("A4", "전문 조립 실패 (조문요소가 있는데 전문=조문내용)",
     "SELECT COUNT(*) FROM 조문 j WHERE 전문=조문내용 AND EXISTS"
     " (SELECT 1 FROM 조문요소 e WHERE e.법령일련번호=j.법령일련번호 AND e.조문순서=j.순서)"),
    ("A5", "항 파싱 누락 (조문내용이 제목뿐인데 조문요소가 없다)",
     "SELECT COUNT(*) FROM 조문 j WHERE 조문여부='조문' AND LENGTH(조문내용)<90"
     " AND 조문내용 LIKE '%)' AND NOT EXISTS"
     " (SELECT 1 FROM 조문요소 e WHERE e.법령일련번호=j.법령일련번호 AND e.조문순서=j.순서)"),
    # ⚠️ **"현행법에 없는 조" 는 게이트가 아니다.** 처음엔 게이트로 뒀는데 전량 적재 후
    #    5,671건이 나왔고, 그 대부분이 **파서 오류가 아니라 코퍼스의 나이**였다.
    #    판례 연대별 발생률이 1990년대 5.4% · 2000년대 2.6% · 2010년대 0.6% · 2020년대 0.2%
    #    로 **27배 기울기**다 — 옛 판례가 그 뒤 전부개정으로 사라진 조를 인용한 것이다
    #    (민사소송법 제557조 · 지방세법 제188조 · 부동산등기법 제131조 …).
    #    이 DB 는 연혁을 안 담으므로(D3) 고칠 수 없고, **고칠 수 없는 빨간불은 표 전체를
    #    무시하게 만든다.** R7·R8 로 추이만 본다.
    #
    #    대신 **파서가 법령명 자리에 조문 원문을 집어넣었는가**를 게이트로 삼는다.
    #    이건 코퍼스의 나이와 무관한 순수한 우리 잘못이고, 헌재 심판대상조문에서 실제로 났다.
    #    술어는 `참조파서.잔해술어` 를 그대로 가져다 쓴다 — 두 곳에 적으면 어긋난 쪽을
    #    아무도 모르고, 그러면 **게이트가 초록인데 데이터가 거짓인** 최악이 된다.
    ("A7", "법령명 자리에 조문 원문이 들어간 의율조문 (파서가 자기 규칙을 어겼다)",
     "SELECT COUNT(*) FROM 의율조문 WHERE 법령명원문 REGEXP :잔해"),
    ("A17", "법령명이 '부칙' 으로 끝나는 행 (부칙이 이름에 붙었다)",
     "SELECT COUNT(*) FROM 의율조문 WHERE 법령명원문 LIKE '%부칙'"),
    # ⚠️ A11 은 '현행이 둘'만 잡고 '현행이 0' 은 못 잡는다. 그런데 **그게 더 나쁘다** —
    #    관용대로 `현행연혁코드='현행'` 을 건 조회에서 그 법이 통째로 안 나오고,
    #    0건이 "그런 법이 없다"로 읽힌다.
    #
    #    처음엔 이걸 "시행예정인데 시행일이 지난 행"으로 물었는데 100건이 나왔다.
    #    재 보니 **그 100건 전부 같은 법령ID 에 현행 행이 따로 있었다** — 시행 전에 다시
    #    개정돼 밀려난 옛 시행예정 버전이고, 법제처의 `eflaw&nw=2` 가 아직 들고 있는 것이다.
    #    원천이 그렇게 주는 것을 우리가 지울 일이 아니므로 **R10 으로 내리고**, 게이트는
    #    실제로 해로운 것만 묻는다.
    ("A18", "현행 행이 하나도 없는 법령ID (조회에서 통째로 빠진다)",
     "SELECT COUNT(*) FROM (SELECT 법령ID FROM 법령 GROUP BY 1"
     "  HAVING SUM(현행연혁코드='현행') = 0"
     "     AND MIN(시행일자) <= date('now','localtime'))"),
    # ⚠️ **겹친 수를 세면 개정이 있었던 날은 반드시 빨갛다.** 원천이 새 판본 시행 전
    #    얼마간 둘 다 '현행'으로 주고 우리는 목록에서 빠진 것을 하루 유예하므로(`정리후보`),
    #    겹침 자체는 **구조적인 창이지 사고가 아니다.** 실측으로 이 게이트 때문에 법령
    #    수집이 최근 20회 중 18회 빨갛게 끝났고, **고칠 수 없는 빨간불은 표 전체를
    #    무시하게 만든다.** 물어야 하는 것은 "겹쳤나"가 아니라 **"겹친 것이 설명되나"**다.
    # ⚠️ **ID 별로 재고 합은 마지막에 낸다.** `Σ행 − Σ ID − Σ후보` 로 쓰면 한 ID 의 후보
    #    과다(−1)와 다른 ID 의 후보 없음(+1)이 **서로를 지워 두 사고가 동시에 초록**이 된다.
    #    불변식은 합이 아니라 ID 하나하나에 있다 — 겹친 법령ID 마다 후보에 안 오른 현행
    #    행이 정확히 하나다. 그 등식의 원시 세 값은 R25 가 그대로 들고 있다.
    # ⚠️ **`ABS` 라 양쪽을 다 센다.** 후보가 넘치는 쪽(그 ID 의 현행이 곧 0이 된다)을
    #    A18 이 대신 잡아 주지 않는다 — A18 은 행이 **지워진 뒤** 남은 그룹만 보므로
    #    유예 중인 첫날에는 0 이고, ID 가 통째로 지워지면 그룹 자체가 없어 영영 0 이다.
    #    개정 창 안에서 폐지가 일어난 날은 이 값이 하루 빨갛다 — 드물고, 그 하루는
    #    "이 법의 현행이 곧 비는데 아직 안 비었다"라는 참말이다.
    # ⚠️ **후보가 영영 안 지워지는 상태는 여기가 아니라 A25·R22 가 진다.** 이 게이트는
    #    후보의 *존재*만 보고 나이를 안 본다. `연속누락` 을 채운 후보는 `정리한다()` 가
    #    같은 트랜잭션에서 지우고 원장에서 빼므로 '완료' 인 채로 늙은 후보는 적재 경로로
    #    생기지 않는다 — 실제로 늙는 길은 둘뿐이고 각각 임자가 있다: 정리를 건너뛴 것은
    #    A25, 부분수신으로 멈춘 것은 R22(며칠째인지까지 준다)다. 여기에 나이 조건을
    #    더하면 같은 사고를 두 곳이 세게 되고, 그건 이 파일이 반복해 피해 온 것이다.
    ("A11", "정리후보로 설명되지 않는 현행 중복 (조회가 두 판본을 준다)",
     "WITH 겹침 AS ("
     "  SELECT 법령ID, COUNT(*) AS 행,"
     "         SUM(EXISTS (SELECT 1 FROM 정리후보 c"
     "                     WHERE c.자료종류='법령' AND c.자료ID=법령.법령일련번호)) AS 후보"
     "    FROM 법령 WHERE 현행연혁코드='현행'"
     "   GROUP BY 법령ID HAVING COUNT(*)>1)"
     " SELECT COALESCE(SUM(ABS(행 - 후보 - 1)), 0) FROM 겹침"),
    ("A12", "조문에 HTML 태그 잔존",
     "SELECT COUNT(*) FROM 조문 WHERE 전문 LIKE '%<br%' OR 전문 LIKE '%</img>%'"
     " OR 전문 LIKE '%<div%' OR 전문 LIKE '%<span%'"),
    ("A13", "고아 의율조문 (자료ID 가 어느 테이블에도 없다)",
     "SELECT COUNT(*) FROM 의율조문 y WHERE NOT EXISTS"
     " (SELECT 1 FROM 판례 WHERE 자료종류='판례' AND 판례일련번호=y.자료ID)"
     " AND NOT EXISTS (SELECT 1 FROM 헌재결정례 WHERE 자료종류='헌재결정례' AND 헌재결정례일련번호=y.자료ID)"
     " AND NOT EXISTS (SELECT 1 FROM 행정심판례 WHERE 자료종류='행정심판례' AND 행정심판례일련번호=y.자료ID)"
     " AND NOT EXISTS (SELECT 1 FROM 법령해석례 WHERE 자료종류='법령해석례' AND 법령해석례일련번호=y.자료ID)"),
    # ── 교차 검증(08 §10)이 요구한 게이트. A7 만으로는 틀린 성공이 안 잡힌다 ──
    # 실측: 이 DB 의 조문요소에서 최대 호번호는 144 다. 200 초과는 원천 오타이거나
    # 파서가 공포번호를 문 것이라 파서가 이미 떨어뜨린다 — 여기 남으면 그 방어가 깨진 것이다.
    ("A15", "공포번호를 호로 오인한 의율조문 (호>200)",
     "SELECT COUNT(*) FROM 의율조문 WHERE 호 > 200"),
    ("A16", "사건번호 문법을 어긴 인용판례",
     "SELECT COUNT(*) FROM 인용판례 WHERE 피인용사건번호 NOT GLOB"
     " '[0-9][0-9]*[가-힣]*[0-9]*'"),
    # ⚠️ **이 게이트가 없어서 헌재 `종국일자='0'` 5,080건이 남았다.** 적재 코드는 이미
    #    `'0'` 을 NULL 로 고쳤는데 **이미 들어간 값을 다시 보는 눈이 없었다.** 코드를
    #    고치는 것과 데이터가 고쳐지는 것은 다른 일이고, 그 차이를 재는 것이 감사다.
    ("A19", "저장 형식(YYYY-MM-DD)이나 달력을 어긴 날짜 값", _날짜위반SQL()),
    # ⚠️ **정규화 안 된 축약형이 하나라도 남으면 그 법원의 판결이 두 이름으로 갈린다.**
    #    `WHERE 법원명='서울고등법원'` 이 조용히 일부를 놓치는 상태로 되돌아간다.
    ("A20", "법원명에 축약형이 남은 판례 (고법·지법·행법·가법·회법)",
     "SELECT COUNT(*) FROM 판례 WHERE 법원명 LIKE '%고법%' OR 법원명 LIKE '%지법%'"
     " OR 법원명 LIKE '%행법%' OR 법원명 LIKE '%가법%' OR 법원명 LIKE '%회법%'"),
    ("A21", "원천 표기를 잃은 판례 (법원명원문이 빈다)",
     "SELECT COUNT(*) FROM 판례 WHERE 법원명 IS NOT NULL AND 법원명원문 IS NULL"),
    # ── 위임 ──
    # ⚠️ **"대상 조가 현행에 없다"를 게이트로 걸면 안 된다.** 실측 21,409행이고 그중
    #    21,495가 인용법령이다 — 옛 버전의 조를 인용한 것이라 **코퍼스의 나이가 만드는
    #    값**이고 줄일 방법이 없다(4,453건은 원본이 시행예정 버전이라 대상이 아직 없다).
    #    R7 이 의율조문에서 정확히 같은 이유로 게이트에서 내려온 자리다. 같은 이유로
    #    "우리 행정규칙에 없는 규칙"(3,975행)도 게이트가 아니다 — 우리는 현행만 담는다.
    #    **고칠 수 없는 빨간불은 표 전체를 무시하게 만든다.** 둘 다 R17·R18 로 추이만 본다.
    #
    #    게이트로 남는 것은 **파서가 자기 규칙을 어겼는가**뿐이다. 넷 다 우리 잘못이고
    #    전량 실측에서 0 이다.
    ("A22", "위임 파서가 자기 규칙을 어긴 행 (조 0 · 대상 조 0 · 대상 없는 시행령/규칙)",
     "SELECT (SELECT COUNT(*) FROM 위임 WHERE 조문번호 <= 0)"
     "     + (SELECT COUNT(*) FROM 위임 WHERE 대상조문번호 = 0)"
     "     + (SELECT COUNT(*) FROM 위임 WHERE 위임구분 IN ('시행령','시행규칙')"
     "          AND 대상제목 IS NULL)"
     "     + (SELECT COUNT(*) FROM 위임 WHERE 위임구분='위임행정규칙'"
     "          AND 대상일련번호 IS NULL)"),
    # ⚠️ **"받았다"와 "남아 있다"는 다르다.** `저장()` 이 법령 부모를 REPLACE 하면
    #    `위임` 의 ON DELETE CASCADE 가 터져 그 법의 위임이 사라지는데, 완료 표시는 남는다.
    #    수집기가 그걸 스스로 고치도록 만들었지만(`위임.받을대상`), **고쳐졌는지 보는 눈이
    #    따로 있어야** 다음에 다른 경로로 같은 일이 생겼을 때 알아챈다.
    ("A23", "완료로 적힌 건수와 실제 위임 행수가 다른 법령 버전",
     "SELECT COUNT(*) FROM 수집상태 s WHERE s.자료종류='법령' AND s.단계='위임'"
     " AND s.상태='완료' AND s.건수 <> (SELECT COUNT(*) FROM 위임 y WHERE y.법령일련번호=s.키)"),
    # ⚠️ **깨진 뷰는 조회할 때야 터진다.** `PRAGMA integrity_check` 도 통과하고
    #    `sqlite_master` 에도 멀쩡히 앉아 있다 — 뷰가 가리키는 컬럼이 사라져도 SQLite 는
    #    아무 말을 안 한다. 그래서 **실제로 조회해 보는 것 말고 확인할 방법이 없다.**
    #    이 DB 의 뷰는 함정을 대신 지키는 인터페이스라(D40) 조용히 죽으면 그 함정이
    #    통째로 되살아난다. 질의가 깨지면 `run()` 이 그걸 위반으로 센다.
    # ⚠️ 빈 것도 위반이다 — 뷰는 사는데 필터가 전부를 걸러내는 상태가 실재할 수 있고,
    #    그러면 "현행 법이 하나도 없다"가 조용히 0건으로 나온다.
    # ⚠️ **목록은 `SCHEMA` 에서 뽑는다** — 손으로 적으면 뷰를 더한 날 그 뷰만 안 지켜진다.
    ("A24", "조회되지 않거나 빈 뷰 (SCHEMA 의 뷰 전부)", _깨진뷰),
    # ⚠️ **이 게이트만 DB 밖을 본다.** 나머지는 전부 현재 DB 안의 등식이라, 행이 사라지면
    #    양변이 함께 사라져 초록이 된다 — 빈 DB 에서 23개 중 21개가 초록이던 이유다.
    #    견줄 것은 백업 세대의 곁기록이고, 그것이 이 레포에서 DB 밖에 사는 유일한 정상이다.
    # ⚠️ **기준선이 없으면 0이다(위반이 아니다).** 첫 백업 전에도 수집은 돌아야 한다.
    #    다만 그 0과 "견줘 봤는데 0" 은 다른 사건이라 R24 가 둘을 갈라 보여준다.
    ("A27", "직전 정상보다 크게 줄어든 핵심표", _급감수),
]

보고: list[tuple[str, str, str]] = [
    ("R1", "자료별 적재 건수",
     "SELECT (SELECT COUNT(*) FROM 법령)||' 법령 / '||(SELECT COUNT(*) FROM 판례)||' 판례 / '"
     "||(SELECT COUNT(*) FROM 헌재결정례)||' 헌재 / '||(SELECT COUNT(*) FROM 행정심판례)||' 행심 / '"
     "||(SELECT COUNT(*) FROM 법령해석례)||' 해석례 / '||(SELECT COUNT(*) FROM 행정규칙)||' 행정규칙'"),
    ("R2", "조문 / 조문요소 / 부칙",
     "SELECT (SELECT COUNT(*) FROM 조문)||' / '||(SELECT COUNT(*) FROM 조문요소)"
     "||' / '||(SELECT COUNT(*) FROM 부칙)"),
    ("R3", "의율조문 / 인용판례 / 파싱실패",
     "SELECT (SELECT COUNT(*) FROM 의율조문)||' / '||(SELECT COUNT(*) FROM 인용판례)"
     "||' / '||(SELECT COUNT(*) FROM 파싱실패)"),
    ("A6", "의율조문 법령ID 미해결 비율 — 구법·폐지법이라 높은 것이 정상",
     "SELECT COALESCE(ROUND(100.0*SUM(법령ID IS NULL)/COUNT(*),1),0)||'%' FROM 의율조문"),
    # ⚠️ 아래 둘이 옛 A7 이다. **절대값을 보지 말고 R8 의 추이를 봐라** — R7 은 코퍼스의
    #    나이가 만드는 값이라 줄일 방법이 없지만, R8(2020년 이후 판례)은 현행법을 인용하는
    #    구간이라 **여기가 늘면 그건 우리 잘못이다.**
    ("R7", "현행법에 없는 조를 가리키는 의율조문 — 코퍼스가 수십 년치라 정상이다",
     "SELECT COUNT(*) FROM 의율조문 y WHERE y.법령ID IS NOT NULL AND y.부칙여부=0"
     " AND NOT EXISTS (SELECT 1 FROM 조문 j JOIN 법령 l USING (법령일련번호)"
     "   WHERE l.법령ID=y.법령ID AND l.현행연혁코드='현행' AND j.조문여부='조문'"
     "     AND j.조문번호=y.조 AND j.조문가지번호=y.가지)"),
    ("R8", "그중 2020년 이후 판례 — **여기가 늘면 파서 회귀다**",
     "SELECT COUNT(*) FROM 의율조문 y JOIN 판례 p ON p.판례일련번호=y.자료ID"
     " WHERE y.자료종류='판례' AND p.선고일자>='2020-01-01'"
     " AND y.법령ID IS NOT NULL AND y.부칙여부=0"
     " AND NOT EXISTS (SELECT 1 FROM 조문 j JOIN 법령 l USING (법령일련번호)"
     "   WHERE l.법령ID=y.법령ID AND l.현행연혁코드='현행' AND j.조문여부='조문'"
     "     AND j.조문번호=y.조 AND j.조문가지번호=y.가지)"),
    # ⚠️ 처음엔 게이트(A14)였는데 전량 적재 후 305건이 나왔고, 그 대부분이 **깨진 이름이
    #    아니라 내가 몰랐던 규범명**이었다 — `정비사업의 시공자 선정기준` · `경찰청과 그
    #    소속기관 직제` · `이주대책 및 생활대책 시행세칙` · `A대학교 학칙`.
    #    꼬리 목록을 늘리는 것은 두더지잡기라 보고값으로 내렸다. **진짜 깨진 이름은 A7 이 잡는다.**
    ("R10", "시행일이 지난 시행예정 행 — 시행 전에 다시 개정돼 밀려난 옛 버전이다",
     "SELECT COUNT(*) FROM 법령 WHERE 현행연혁코드='시행예정'"
     " AND 시행일자 <= date('now','localtime')"),
    ("R11", "A19 가 빨갈 때 어느 컬럼인가", _날짜위반SQL(상세=True)),
    # ⚠️ **줄었는지가 아니라 남은 것이 설명 가능한지를 본다.** 정규화 후 224 → 127종이고,
    #    남는 것 중에는 `중앙해양안전심판원`(법원이 아니다)·`부산고등법원 창원재판부`
    #    (괄호 대신 '재판부'로 적은 표기)처럼 규칙 밖의 것이 있다. 규칙을 늘려 잡으려
    #    들면 두더지잡기가 되므로 **드러내기만 한다** — 조용히 뭉개지 않는 것이 요점이다.
    ("R12", "법원명 종수와 규칙 밖 이름",
     "SELECT (SELECT COUNT(DISTINCT 법원명) FROM 판례)||'종 · 규칙 밖 '"
     "||(SELECT COALESCE(GROUP_CONCAT(법원명||' '||n, ' · '), '없음') FROM"
     "   (SELECT 법원명, COUNT(*) n FROM 판례 WHERE 법원명 NOT LIKE '%법원'"
     "      AND 법원명 NOT LIKE '%지원' AND 법원명 NOT LIKE '%)' GROUP BY 1))"),
    ("R13", "적재 시 정규화된 판례 (법원명 <> 법원명원문)",
     "SELECT COUNT(*) FROM 판례 WHERE 법원명 IS NOT 법원명원문"),
    ("R14", "위임구분별 건수 — 인용법령이 가장 많은 것이 정상이다(위임이 아니다)",
     "SELECT COALESCE(GROUP_CONCAT(COALESCE(위임구분,'미상')||' '||n, ' · '),'없음') FROM"
     " (SELECT 위임구분, COUNT(*) n FROM 위임 GROUP BY 1 ORDER BY n DESC)"),
    # ⚠️ **셋을 가른다.** 원천 MST 가 우리에게 없는 것(옛 버전이라 정상)과, 제목으로도
    #    못 되찾은 것(진짜 미해결)과, 아예 대상을 못 정한 것(병렬 배열이라 비운 것)은
    #    원인이 다르다. 한 숫자로 합치면 파서 회귀가 코퍼스의 나이에 묻힌다.
    # ⚠️ 세 번째 값은 "대상을 모른다"가 아니라 **"MST 없이 이름으로만 알아냈다"**다.
    #    `대상제목` 은 채워져 있고 `대상법령ID` 도 대개 붙는다 — 조회에 쓸 수 있다.
    ("R15", "위임 대상 해결률 — MST 일치 / 법령ID 되찾음 / 이름으로만",
     "SELECT (SELECT COUNT(*) FROM 위임)||'행 · MST '"
     "||(SELECT COALESCE(ROUND(100.0*SUM(EXISTS(SELECT 1 FROM 법령 l"
     "     WHERE l.법령일련번호=y.대상일련번호))/COUNT(*),1),0) FROM 위임 y"
     "   WHERE y.위임구분<>'위임행정규칙')||'% · 법령ID '"
     "||(SELECT COALESCE(ROUND(100.0*SUM(대상법령ID IS NOT NULL)/COUNT(*),1),0) FROM 위임"
     "   WHERE 위임구분<>'위임행정규칙')||'% · 이름으로만 '"
     "||(SELECT COUNT(*) FROM 위임 WHERE 대상일련번호 IS NULL)"),
    ("R17", "대상 조가 현행에 없는 위임 — 옛 버전을 인용한 것이라 정상이다",
     "SELECT COALESCE(GROUP_CONCAT(위임구분||' '||n, ' · '),'없음') FROM"
     " (SELECT COALESCE(y.위임구분,'미상') 위임구분, COUNT(*) n FROM 위임 y"
     "    WHERE y.대상법령ID IS NOT NULL AND y.대상조문번호 IS NOT NULL"
     "      AND NOT EXISTS (SELECT 1 FROM 조문 j JOIN 법령 l USING (법령일련번호)"
     "        WHERE l.법령ID=y.대상법령ID AND l.현행연혁코드='현행' AND j.조문여부='조문'"
     "          AND j.조문번호=y.대상조문번호 AND j.조문가지번호=y.대상조문가지번호)"
     "  GROUP BY 1 ORDER BY n DESC)"),
    ("R18", "우리가 안 담는 행정규칙을 가리키는 위임 — 현행만 담으므로 정상이다",
     "SELECT COUNT(*) FROM 위임 y WHERE y.위임구분='위임행정규칙'"
     " AND y.대상일련번호 IS NOT NULL AND NOT EXISTS"
     " (SELECT 1 FROM 행정규칙 r WHERE r.행정규칙일련번호=y.대상일련번호)"),
    ("R19", "위임이 0행인 법령 버전 — 하위 규범에 아무것도 안 미룬 법이다",
     "SELECT COUNT(*) FROM 수집상태 WHERE 자료종류='법령' AND 단계='위임'"
     " AND 상태='완료' AND 건수=0"),
    ("R16", "위임을 받은 법령 버전 / 전체 — 시행예정도 받아야 한다",
     "SELECT (SELECT COUNT(*) FROM 수집상태 WHERE 자료종류='법령' AND 단계='위임'"
     "   AND 상태='완료')||' / '||(SELECT COUNT(*) FROM 법령)"),
    ("R9", "법령명이 흔한 꼬리로 안 끝나는 행 — 기준·직제·세칙·학칙 등이라 대개 정상이다",
     "SELECT COUNT(*) FROM 의율조문 WHERE 법령명원문 NOT LIKE '%법'"
     " AND 법령명원문 NOT LIKE '%률' AND 법령명원문 NOT LIKE '%령'"
     " AND 법령명원문 NOT LIKE '%규칙' AND 법령명원문 NOT LIKE '%규정'"
     " AND 법령명원문 NOT LIKE '%조례' AND 법령명원문 NOT LIKE '%헌장'"
     " AND 법령명원문 NOT LIKE '%협약' AND 법령명원문 NOT LIKE '%조약'"
     " AND 법령명원문 NOT LIKE '%규약' AND 법령명원문 NOT LIKE '%협정'"
     " AND 법령명원문 NOT LIKE '%의정서' AND 법령명원문 NOT LIKE '%준칙'"
     " AND 법령명원문 NOT LIKE '%고시' AND 법령명원문 NOT LIKE '%훈령'"
     " AND 법령명원문 NOT LIKE '%예규' AND 법령명원문 NOT LIKE '%지침'"),
    ("A8", "인용판례 피인용자료ID 미해결 비율 — 적재 범위 밖 선례가 많다",
     "SELECT COALESCE(ROUND(100.0*SUM(피인용자료ID IS NULL)/COUNT(*),1),0)||'%' FROM 인용판례"),
    ("A9", "파싱실패 사유별",
     "SELECT COALESCE(GROUP_CONCAT(사유||' '||n, ' · '),'없음') FROM"
     " (SELECT 사유, COUNT(*) n FROM 파싱실패 GROUP BY 1)"),
    ("R4", "자료종류별 의율조문 — 헌재가 낮은 것이 정상(원천 필드가 9~14%만 채워진다)",
     "SELECT COALESCE(GROUP_CONCAT(자료종류||' '||n, ' · '),'없음') FROM"
     " (SELECT 자료종류, COUNT(*) n FROM 의율조문 GROUP BY 1)"),
    ("A10", "대표 검색어 — 0 이면 본문이 비었거나 전문 조립이 실패한 것이다",
     "SELECT '가명정보 '||(SELECT COUNT(*) FROM 조문 WHERE 전문 LIKE '%가명정보%')"
     "||' · 과태료 '||(SELECT COUNT(*) FROM 조문 WHERE 전문 LIKE '%과태료%')"
     "||' · 위임 '||(SELECT COUNT(*) FROM 조문 WHERE 전문 LIKE '%위임%')"),
    ("R5", "수집실패 중 '없음'으로 확정된 것 (재시도하지 않는다)",
     "SELECT COUNT(*) FROM 수집실패 WHERE 실패종류='없음'"),
    ("R6", "행정규칙 조문형식여부 N — 조문번호 NULL 인 1행으로 들어간다",
     "SELECT COUNT(*) FROM 행정규칙 WHERE 조문형식여부='N'"),
    # ⚠️ **읽는 코드가 없는 컬럼은 조용히 썩는다.** `수집일시` 를 남기기로 한 이유가
    #    "원천이 복원해 주지 못하는 유일한 값"이라서인데(D39), 아무도 안 보면 그 값이
    #    맞는지도 모른다. D31 이 `해석기관명` 을 지우면서 그 감시를 `verify.py` V20 으로
    #    옮긴 것과 같은 수법이다 — **안 읽히는 컬럼을 감시되는 컬럼으로 바꾼다.**
    # ⚠️ 여기서 자료마다 날짜가 크게 벌어지는 것이 정상이다. 지금은 **증분 갱신이 없어서**
    #    이미 있는 행을 다시 받지 않는다(`lsHstInf&regDt` 가 `verify.py` V8 에만 있고 수집
    #    경로엔 없다). 그래서 이 값은 "그 자료를 마지막으로 새로 받은 때"에 가깝고,
    #    **원천에서 바뀐 행은 영원히 안 고쳐진다.** 그 구멍의 크기가 여기 보인다.
    ("R20", "자료별 마지막 수집일시 — 벌어져 있으면 증분 갱신이 없다는 뜻이다",
     "SELECT '법령 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 법령),'-')"
     "||' · 판례 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 판례),'-')"
     "||' · 헌재 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 헌재결정례),'-')"
     "||' · 행심 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 행정심판례),'-')"
     "||' · 해석례 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 법령해석례),'-')"
     "||' · 행정규칙 '||COALESCE((SELECT substr(MAX(수집일시),6,11) FROM 행정규칙),'-')"),
    # ⚠️ **이 수치가 문서에 박혀 있어서 두 곳이 갈렸다.** `references` 24,212 · 스키마 주석
    #    24,202 · 실측 24,333 — 누가 틀린 게 아니라 코퍼스가 자란 것이다. 변동값은 문서가
    #    아니라 여기 있어야 한다("건수는 문서에 안 적는다" 원칙).
    ("R21", "판례 중 하급심 — '대법원 출처'가 '대법원 판결'이 아니라는 증거다",
     "SELECT COALESCE((SELECT COUNT(*) FROM 판례 WHERE 법원명<>'대법원'),0)||'건 ('"
     "||COALESCE((SELECT ROUND(100.0*SUM(법원명<>'대법원')/COUNT(*),1) FROM 판례),0)||'%)'"),
    # ⚠️ **여기가 며칠째 그대로면 정리가 멈춘 채 굳은 것이다.** 부분 수신 자체는 흔한
    #    사건이라 게이트가 아니지만(A25 주석), 그 상태로 **머물러 있는 것**은 다르다 —
    #    기준선도 안 갱신되므로 A1 마저 옛 값끼리 맞아 초록이라, 이 줄이 유일한 단서다.
    # ⚠️ **게이트가 아니라 보고값이다.** 원천이 자료를 거두는 것은 사고가 아니라 원천의
    #    권한이고, 우리가 남기기로 한 것은 판단이지 결함이 아니다. 다만 **이 수가 늘면
    #    코퍼스가 원천과 갈라지고 있다는 뜻**이라 추이를 본다.
    ("R23", "원천이 거뒀지만 우리가 남긴 자료 (인용 전에 확인이 필요한 것)",
     "SELECT COALESCE(GROUP_CONCAT(자료종류||' '||n, ' · '), '없음') FROM ("
     + " UNION ALL ".join(
         f"SELECT '{종}' 자료종류, COUNT(*) n FROM {테} WHERE 원천철회일 IS NOT NULL"
         for 종, (테, _, _) in 저장.본문테이블.items())
     + ") WHERE n > 0"),
    ("R22", "정리가 부분 수신으로 멈춘 자료종류와 며칠째인가",
     "SELECT COALESCE(GROUP_CONCAT(자료종류||' '||substr(COALESCE(상태시작일시,갱신일시),1,10)"
     "||'부터 '||CAST(julianday('now','localtime')"
     "  - julianday(COALESCE(상태시작일시,갱신일시)) AS INT)||'일', ' · '), '없음')"
     " FROM 수집상태 WHERE 단계='정리' AND 상태='부분수신'"),
    # A27 은 수만 준다. **어느 표가 얼마나 줄었는지**가 없으면 사람이 빨간불 앞에서 할 수
    # 있는 일이 없고, "기준선 없음" 인지도 여기서만 보인다.
    ("R24", "직전 정상(백업 세대) 대비", _급감상세),
    # A11 은 **설명 안 되는 잔차**만 준다. 창이 얼마나 열려 있고 어느 쪽으로 어긋났는지는
    # 여기서만 보인다 —
    # 원천이 새 판본 시행 전 얼마간 옛 판본과 새 판본을 둘 다 '현행'으로 주고, 우리는
    # 목록에서 빠진 것을 하루 유예한 뒤 지운다. 그래서 **법마다 한 판본만 남고 나머지는
    # `정리후보` 에 올라 있어야 한다** — `정리후보 = 행 − 법령ID` 가 그 등식이고,
    # 어긋난 만큼이 "겹쳤는데 지울 예정도 아닌" 설명 안 되는 행이다.
    ("R25", "현행이 겹친 법령ID · 그 행 · 정리후보 (행 − ID = 정리후보 여야 한다)",
     "WITH 둘 AS (SELECT 법령ID FROM 법령 WHERE 현행연혁코드='현행'"
     "             GROUP BY 법령ID HAVING COUNT(*)>1),"
     "     행 AS (SELECT 법령일련번호 FROM 법령 WHERE 현행연혁코드='현행'"
     "             AND 법령ID IN (SELECT 법령ID FROM 둘))"
     " SELECT (SELECT COUNT(*) FROM 둘)||' · '||(SELECT COUNT(*) FROM 행)||' · '"
     " ||(SELECT COUNT(*) FROM 행 JOIN 정리후보 c"
     "     ON c.자료종류='법령' AND c.자료ID=행.법령일련번호)"),
    ("R26", "SCHEMA 의 뷰 (조회되고 비지 않는가)", lambda conn: (
        lambda 전부, 난것: f"{전부 - len(난것)}/{전부} 정상"
        + (" · " + " · ".join(난것) if 난것 else ""))(*_깨진뷰상세(conn))),
    ("R27", "위임행정규칙 해결률 (ID / 일련번호 / 이름후보 / 미해결)",
     "WITH 분류 AS (SELECT CASE"
     " WHEN EXISTS (SELECT 1 FROM 행정규칙 r WHERE r.행정규칙ID=y.대상행정규칙ID"
     "   AND r.현행여부='Y') THEN 'ID'"
     " WHEN EXISTS (SELECT 1 FROM 행정규칙 r WHERE r.행정규칙일련번호=y.대상일련번호) THEN '일련번호'"
     " WHEN EXISTS (SELECT 1 FROM 행정규칙 r WHERE r.행정규칙명=y.대상제목"
     "   AND r.현행여부='Y') THEN '이름후보' ELSE '미해결' END AS 해결"
     " FROM 위임 y WHERE y.위임구분='위임행정규칙')"
     " SELECT 'ID '||COUNT(CASE WHEN 해결='ID' THEN 1 END)"
     " ||' · 일련번호 '||COUNT(CASE WHEN 해결='일련번호' THEN 1 END)"
     " ||' · 이름후보 '||COUNT(CASE WHEN 해결='이름후보' THEN 1 END)"
     " ||' · 미해결 '||COUNT(CASE WHEN 해결='미해결' THEN 1 END) FROM 분류"),
]


def run(conn) -> tuple[list, list, int]:
    # SQLite 에 REGEXP 가 기본으로 없다. 게이트 하나가 그걸 필요로 하는데, GLOB 로 쓰면
    # `제1회토지공채조례` 같은 정상 이름을 오탐한다(반복 표현이 없어서다).
    import re as _re

    from law import refparser as 참조파서

    _잔해 = _re.compile(참조파서._원문잔해.pattern)
    conn.create_function("REGEXP", 2, lambda p, s: bool(_잔해.search(s or "")))
    매개 = {"잔해": ""}

    _직전캐시.pop(id(conn), None)      # 같은 연결로 다시 부르면 다시 잰다
    결과게이트, 결과보고, 실패 = [], [], 0
    for 번호, 이름, sql in 게이트:
        # ⚠️ **질의가 깨지는 것 자체가 위반이다.** 뷰나 컬럼이 사라지면 여기서
        #    `OperationalError` 가 나는데, 그냥 두면 **감사가 통째로 죽어서 나머지
        #    게이트의 답도 못 듣는다.** 하나가 깨진 것과 전부를 모르는 것은 다르다.
        try:
            v = (sql(conn) if callable(sql)
                 else conn.execute(sql, 매개 if ":잔해" in sql else ()).fetchone()[0] or 0)
        except sqlite3.Error as e:
            결과게이트.append((번호, f"{이름} · 🔴 질의가 깨졌다: {e}", 1, False))
            실패 += 1
            continue
        실패 += v != 0
        결과게이트.append((번호, 이름, v, v == 0))
    for 번호, 이름, sql in 보고:
        try:
            결과보고.append((번호, 이름,
                           sql(conn) if callable(sql) else conn.execute(sql).fetchone()[0]))
        except sqlite3.Error as e:
            결과보고.append((번호, 이름, f"🔴 질의가 깨졌다: {e}"))
    return 결과게이트, 결과보고, 실패


_해석 = {
    "A7": "파서가 틀린 성공을 냈다 — 데이터는 있는데 거짓이다. 가장 위험한 빨강이다.",
    "A15": "파서가 틀린 성공을 냈다 — 데이터는 있는데 거짓이다. 가장 위험한 빨강이다.",
    "A16": "파서가 틀린 성공을 냈다 — 데이터는 있는데 거짓이다. 가장 위험한 빨강이다.",
    "A17": "파서가 틀린 성공을 냈다 — 데이터는 있는데 거짓이다. 가장 위험한 빨강이다.",
    "A19": "저장 형식이 어긋났다 — 날짜가 'YYYY-MM-DD' 를 벗어났다. 어느 컬럼인지는 R11."
           " 그 컬럼의 범위 조건과 연도별 집계가 조용히 행을 빠뜨린다.",
    "A20": "저장 형식이 어긋났다 — 법원명에 축약형이 남았다."
           " `WHERE 법원명='서울고등법원'` 이 그만큼을 놓치고 그게 '없다'로 읽힌다.",
    "A24": "뷰가 깨졌다 — 어느 뷰인지는 R26. 조문 조회의 두 조건(현행·조문여부)을 지키던"
           " 인터페이스가 사라진 것이라, 그때부터 시행예정과 장 제목이 조용히 섞인다.",
    "A11": "겹친 현행이 정리후보로 설명되지 않는다. 겹침 자체는 원천이 새 판본 시행 전"
           " 둘 다 주는 구조적 창이라 정상이고, 여기 잡히는 것은 **그 창으로 설명 안 되는"
           " 만큼**이다. 후보가 모자라면 정리가 멈춘 것이고(A25·R22 를 봐라), 후보가"
           " 넘치면 그 법령ID 의 현행이 곧 통째로 빈다. 어느 쪽인지는 R25 가 가른다.",
    "A25": "정리를 건너뛰었다 — 원천이 이상해 삭제를 멈췄다. 원천이 돌아오면 저절로 풀린다.",
}


def 감사한다(conn) -> tuple[list, list, int]:
    """`run` 을 돌리고 **그 결과를 그대로 화면에 낸다.** 판정은 부르는 쪽이 센다.

    ⚠️ **부르는 쪽이 `run` 을 또 부르면 안 된다.** 게이트 하나가 30만 행을 훑으므로
    두 번 도는 것은 그냥 두 배가 아니라 자동 수집의 꼬리를 몇 분 늘리는 일이고,
    그 사이에 DB 가 바뀌면 **화면에 낸 표와 곁기록의 값이 갈린다.**

    ⚠️ **출력을 여기 모아 둔 이유.** 곁기록(`result.json`)과 화면과 `메타` 판정이
    같은 한 번의 결과를 써야 한다. 셋이 각자 `run` 을 부르던 모양으로 되돌리지 마라.
    """
    게, 보, 실패 = run(conn)

    print(f"\n{'#':<5} {'게이트 (0이어야 한다)':<52} 값")
    for 번호, 이름, v, ok in 게:
        print(f"{번호:<5} {이름:<52} {v:>10,}  {'🟢' if ok else '🔴'}")
    print(f"\n{'#':<5} {'보고값 (추이를 본다)':<52} 값")
    for 번호, 이름, v in 보:
        print(f"{번호:<5} {이름:<52} {v}")
    if 실패:
        print(f"\n🔴 게이트 {실패}건 위반.")
        # ⚠️ **해석을 여기 두는 이유.** 게이트 이름은 "무엇을 셌나"이지 "그래서 무슨 일이
        #    났나"가 아니다. 그 해석을 지면(SKILL.md)에 적으면 게이트를 고친 날 지면만
        #    낡고, 정작 빨간불을 보는 사람은 지면을 안 연다 — 이 줄은 빨간 순간에만,
        #    공짜로 읽힌다.
        for 번호, 말 in _해석.items():
            if any(n == 번호 and not ok for n, _, _, ok in 게):
                print(f"   {번호}: {말}")
    else:
        print(f"\n🟢 게이트 {len(게)}건 전부 통과.")
    return 게, 보, 실패


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    a = ap.parse_args(argv)
    *_, 실패 = 감사한다(연결.connect(a.db))
    return 1 if 실패 else 0


if __name__ == "__main__":
    raise SystemExit(_main())
