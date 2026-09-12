#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""적재된 데이터가 온전한가 — **네트워크를 안 타고 실제 DB 만 본다.**

**게이트와 보고값을 반드시 가른다.** 원천이 보장하지 않는 것을 게이트로 걸면 고칠 수 없는
빨간불이 생기고, **고칠 수 없는 빨간불은 표 전체를 무시하게 만든다.**

원장 두 표가 왜 나뉘어 있는지도 여기 적는다 — 스키마 주석은 조회자가 읽는 자리라
적재 설계는 거기 있으면 안 된다.

- **`수집실패` 는 낱개, `수집상태` 는 패스다.** 의안 하나를 못 받은 것은 낱개 원장이고,
  "이번 패스는 통째로 교체를 안 했다"는 걸 자료가 없다. 낱개 원장에 넣으면 대상키가
  의안번호도 회의id도 아닌 것이 섞인다.
- **`수집상태.상태='건너뜀'` 은 A12 만 본다.** 교체를 안 한 표는 행수 등식을 그대로
  만족시키므로 다른 게이트로는 구조적으로 안 잡힌다.
- **`수집실패.상세` 에 게이트를 걸지 마라 — 다음 재시도가 덮어쓴다.** "값이 들어올
  때까지 계속 빨갛다"를 약속하는 자리는 `실패종류='막힘'` 이다.

스키마에서 옮긴 운영 기록:
- 수집실패는 성공하면 행을 삭제하므로 비어 있는 것이 정상 상태다.
- '없음'은 재시도하지 않고, '막힘'은 사람이 고쳐야 하며, '보류'는 재시도 상한에 닿은 항목을 사람이 확인한 뒤 더는 묻지 않기로 한 상태다.
- 수집상태의 '건너뜀'은 원천 행수가 줄어 교체를 생략한 결과이며, 건수는 얼마나 줄었는지 남긴다.
- 갱신일시는 매 실행 덮이고 상태시작일시는 같은 상태가 이어지는 동안 유지된다 — 하루의 일시적 원천 이상과 장기간의 원천 변화를 구분하려는 기록이다.
"""

from __future__ import annotations

import importlib.util
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from congress import db  # noqa: E402

재시도상한 = db.재시도상한  # 정의는 원장을 소유한 모듈에 있다 — 큐가 같은 값을 지켜야 A11 이 참이다


드리프트질의 = db.드리프트질의  # 정의는 SCHEMA 를 소유한 모듈에 있다

# ── 직전 정상과 견주는 축 ────────────────────────────────────────────────────
#
# 위 게이트는 전부 **현재 DB 안에서** 등식을 본다. 그래서 행이 사라지면 등식의 양변이
# 함께 사라져 오히려 잘 맞는다 — 실측(2026-08-28)으로 빈 DB 에서 13개 중 12개가 초록이고
# 의안 한 건만 남기면 13개 전부 초록이었다. **게이트를 더 다는 것은 이 성질을 안 바꾼다.**
# 축을 하나 넣는다: *감사는 직전 정상과 비교한다.* 정책(무엇이 급감인가)은 세대를 아는
# 쪽이 소유한다 — 여기 숫자를 복사해 두면 두 벌이 갈린다.

_코퍼스이름 = "congress"
_직전캐시: dict[int, tuple] = {}


def _백업모듈():
    """`backup.py` 를 **파일 하나만 이름 붙여** 불러온다.

    ⚠️ **레포 루트를 `sys.path` 에 올리지 마라** — 네 스킬의 `Scripts/` 에 같은 이름이
    여럿 있어, 먼저 import 된 쪽이 다른 스킬을 조용히 깨뜨린다(테스트에서 실측했다).
    """
    if "backup" in sys.modules:
        return sys.modules["backup"]
    경로 = Path(__file__).resolve().parents[2] / "Scripts" / "backup.py"
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


def _깨진뷰(conn) -> int:
    return len(_깨진뷰상세(conn)[1])


def _깨진뷰상세(conn) -> tuple[int, list[str]]:
    """⚠️ **깨진 뷰는 조회할 때야 터진다.** `PRAGMA integrity_check` 도 통과하고
    `sqlite_master` 에도 멀쩡히 앉아 있다 — 뷰가 가리키는 컬럼이 사라져도 SQLite 는
    아무 말을 안 한다. **실제로 조회해 보는 것 말고 확인할 방법이 없다.**

    ⚠️ 빈 것도 위반이다 — 뷰는 사는데 필터가 전부를 걸러내는 상태가 실재할 수 있고,
    그러면 "법률안이 하나도 없다"가 조용히 0건으로 나온다. 여기 뷰 둘은 **정상적으로
    비는 날이 없다**(law 쪽에는 그런 뷰가 있어 예외 목록을 든다).
    """
    이름들 = _뷰이름들(db.SCHEMA)
    난것 = []
    for v in 이름들:
        try:
            빈가 = conn.execute(f'SELECT COUNT(*) FROM "{v}"').fetchone()[0] == 0
        except sqlite3.Error as e:
            난것.append(f"{v}({e})")
            continue
        if 빈가:
            난것.append(f"{v}(비었다)")
    return len(이름들), 난것


# ── 게이트 — 0이 아니면 실패다 ────────────────────────────────────────────────
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
    (
        "A1",
        "의안번호 구멍 수",
        # `수집실패` 에 '없음' 으로 설명되는 번호는 구멍이 아니다 — 원천에 실제로 없다.
        # ⚠️ 상한이 **적재된 MAX** 라, 꼬리 100개가 통째로 빠지면 MAX 도 같이 작아져 0이 된다.
        #    그래서 `collect_보완` 이 원천에서 위로 탐침해 꼬리를 먼저 채운다(03 §1).
        """
        WITH 범위(n) AS (
            SELECT MIN(CAST(의안번호 AS INTEGER)) FROM 의안 WHERE 의안번호 NOT GLOB '*[^0-9]*'
            UNION ALL
            SELECT n+1 FROM 범위
            WHERE n < (SELECT MAX(CAST(의안번호 AS INTEGER)) FROM 의안 WHERE 의안번호 NOT GLOB '*[^0-9]*')
        )
        SELECT COUNT(*) FROM 범위
        WHERE NOT EXISTS (SELECT 1 FROM 의안 b WHERE b.의안번호 = CAST(범위.n AS TEXT))
          AND NOT EXISTS (SELECT 1 FROM 수집실패 f
                          WHERE f.대상종류='의안상세' AND f.실패종류='없음'
                            AND f.대상키 = CAST(범위.n AS TEXT))
        """,
    ),
    ("A2", "대안이 자기 자신을 가리키는 행", "SELECT COUNT(*) FROM 의안 WHERE 대안의안번호 = 의안번호"),
    (
        "A3",
        "제안자구분='의원'인데 발의자가 0인 의안",
        "SELECT COUNT(*) FROM 의안 b WHERE b.제안자구분='의원'"
        " AND NOT EXISTS (SELECT 1 FROM 발의자 p WHERE p.의안번호=b.의안번호)",
    ),
    ("A4", "수집실패 '막힘'", "SELECT COUNT(*) FROM 수집실패 WHERE 실패종류='막힘'"),
    (
        "A5",
        "발언이 0건인 회의",
        "SELECT COUNT(*) FROM 회의 m WHERE NOT EXISTS (SELECT 1 FROM 발언 s WHERE s.회의id=m.회의id)",
    ),
    (
        "A6",
        "본회의 심사행인데 의안.처리결과가 NULL",
        "SELECT COUNT(*) FROM 의안심사 s JOIN 의안 b USING (의안번호)"
        " WHERE s.단계='본회의' AND b.처리결과 IS NULL",
    ),
    (
        "A7",
        "의원에 없는 의원코드 (FK 이중 확인)",
        "SELECT (SELECT COUNT(*) FROM 발의자 x WHERE x.의원코드 NOT IN (SELECT 의원코드 FROM 의원))"
        " + (SELECT COUNT(*) FROM 표결 x WHERE x.의원코드 NOT IN (SELECT 의원코드 FROM 의원))"
        " + (SELECT COUNT(*) FROM 발언 x WHERE x.의원코드 IS NOT NULL"
        "    AND x.의원코드 NOT IN (SELECT 의원코드 FROM 의원))",
    ),
    (
        "A8",
        "표결 행이 있는데 표결집계가 없는 의안",
        "SELECT COUNT(DISTINCT v.의안번호) FROM 표결 v"
        " WHERE NOT EXISTS (SELECT 1 FROM 표결집계 a WHERE a.의안번호=v.의안번호)",
    ),
    (
        "A9",
        "가결·부결인데 본회의 심사행이 없는 의안",
        # ⚠️ A6 의 역방향이다. A6 은 본회의 행에서 출발하므로 **행이 전부 지워지면 모집단이
        #    0이 되어 초록**인데, 그게 하필 `의안심사` DELETE 를 단계로 안 좁혔을 때의 사고다.
        # ⚠️ 법률안에만 묻는다 — `의안심사` 의 원천이 법률안 전용이라 비법률안은 본회의 행이
        #    구조적으로 없다. 비법률안 처리결과를 ALLBILL 로 채우자(#86) 가결 결의안이 여기 걸렸다.
        "SELECT COUNT(*) FROM 의안 b WHERE b.의안종류='법률안'"
        " AND b.처리결과 IN ('원안가결','수정가결','부결')"
        " AND NOT EXISTS (SELECT 1 FROM 의안심사 s WHERE s.의안번호=b.의안번호 AND s.단계='본회의')",
    ),
    (
        "A10",
        "표결집계는 있는데 표결 명단이 0행인 의안",
        # ⚠️ A8 의 역방향. A8 은 표결에서 출발하므로 **명단이 통째로 안 들어와도 0이다.**
        #    그러면 "누가 찬성했나"가 전부 빈손인데 게이트는 초록이다.
        "SELECT COUNT(*) FROM 표결집계 a"
        " WHERE NOT EXISTS (SELECT 1 FROM 표결 v WHERE v.의안번호=a.의안번호)",
    ),
    (
        "A11",
        f"재시도 상한({재시도상한}회)에 닿아 포기한 항목",
        # ⚠️ A4 는 '막힘' 만 본다. 상한에 닿아 큐에서 빠진 것은 영영 안 받아지는데 '막힘' 이
        #    아니라서 초록이었다. **"포기했다"는 사람이 봐야 하는 상태다.**
        f"SELECT COUNT(*) FROM 수집실패 WHERE 실패종류='재시도' AND 시도횟수 >= {재시도상한}",
    ),
    (
        "A12",
        "원천이 줄어 교체를 건너뛴 자리",
        # ⚠️ **이 사고는 다른 어느 게이트에도 안 걸린다.** 교체를 건너뛰면 그 표는 옛 값을
        #    **그대로 들고 있어서** 행수 등식이 전부 맞는다 — 나머지 게이트가 전부 초록인
        #    채로 의원위원회 배정이나 현직 명단만 며칠씩 낡는다. 여기만 그걸 안다.
        # ⚠️ **사람이 손대서 푸는 빨간불이 아니다.** 원인은 원천이고, 원천이 돌아오면
        #    다음 수집이 '완료' 로 덮어 저절로 풀린다. 며칠째 안 풀리면 R14 가 그걸 준다.
        "SELECT COUNT(*) FROM 수집상태 WHERE 상태='건너뜀'",
    ),
    (
        "A13",
        "직전 정상보다 크게 줄어든 핵심표",
        # ⚠️ **이 게이트만 DB 밖을 본다.** 나머지는 전부 현재 DB 안의 등식이라, 행이
        #    사라지면 양변이 함께 사라져 초록이 된다 — 이 파일의 나머지 열세 줄이
        #    빈 DB 에서 열둘까지 초록인 이유가 그것이다. 견줄 것은 백업 세대의 곁기록이다.
        # ⚠️ **기준선이 없으면 0이다(위반이 아니다).** 첫 백업 전에도 수집은 돌아야 한다.
        #    다만 그 0과 "견줘 봤는데 0" 은 다른 사건이라 R15 가 둘을 갈라 보여준다.
        _급감수,
    ),
    # ⚠️ 이 DB 의 뷰는 함정을 대신 지키는 인터페이스다 — `법률안제안주체` 가 없으면
    #    발의자 조인이 가결 법안의 절반을 지우고, `의안회의` 가 없으면 위원장 대안의
    #    논의가 통째로 0건이 된다. **조용히 죽으면 그 함정이 통째로 되살아난다.**
    ("A14", "조회되지 않거나 빈 뷰 (SCHEMA 의 뷰 전부)", _깨진뷰),
]

# ── 보고값 — 0이 아니어도 정상이다. 추이를 본다 ───────────────────────────────
보고: list[tuple[str, str, str]] = [
    ("R1", "의안 처리결과 값 목록", "SELECT GROUP_CONCAT(v,' · ') FROM (SELECT COALESCE(처리결과,'NULL')||' '||COUNT(*) AS v FROM 의안 GROUP BY 처리결과 ORDER BY COUNT(*) DESC)"),
    ("R2", "의안심사 처리결과 값 수", "SELECT COUNT(DISTINCT 처리결과) FROM 의안심사"),
    ("R3", "정당이 NULL인 의원", "SELECT COUNT(*) FROM 의원 WHERE 정당 IS NULL"),
    (
        "R4",
        "발언 의원코드 NULL 비율 (회의종류 × 소위여부)",
        # ⚠️ **회의종류만으로 가르면 안 된다** — 소위 회의도 회의종류가 '상임위원회' 라
        #    전체회의(35%)와 소위(15%)가 한 버킷에 섞여 25% 근처로 뭉개진다.
        #    그러면 어느 쪽이 망가져도 눈에 안 띈다.
        """
        SELECT COALESCE(GROUP_CONCAT(v, ' · '), '회의 없음') FROM (
          SELECT 분류 || ' ' || ROUND(100.0*SUM(빔)/COUNT(*), 1) || '%' AS v
          FROM (
            SELECT m.회의종류 || CASE WHEN w.상위위원회 IS NULL THEN '' ELSE '(소위)' END AS 분류,
                   (s.의원코드 IS NULL) AS 빔
            FROM 발언 s JOIN 회의 m USING (회의id) JOIN 위원회 w ON w.위원회명 = m.위원회명
          )
          GROUP BY 분류 ORDER BY 분류)
        """,
    ),
    ("R5", "회의가 있는 날짜 수", "SELECT COUNT(DISTINCT 회의일자) FROM 회의"),
    ("R6", "수집실패 (종류별)", "SELECT COALESCE(GROUP_CONCAT(v,' · '),'없음') FROM (SELECT 실패종류||' '||COUNT(*) AS v FROM 수집실패 GROUP BY 실패종류)"),
    (
        "R7",
        "회의의안 중 의안에 없는 번호",
        "SELECT COUNT(*) FROM 회의의안 x WHERE NOT EXISTS (SELECT 1 FROM 의안 b WHERE b.의안번호=x.의안번호)",
    ),
    ("R8", "규모 (의안 · 발언 · 회의)", "SELECT (SELECT COUNT(*) FROM 의안)||' · '||(SELECT COUNT(*) FROM 발언)||' · '||(SELECT COUNT(*) FROM 회의)"),
    (
        "R10",
        "표결집계.재적수 <> 표결 행수인 의안",
        # ⚠️ **원천의 한계라 게이트로 걸면 안 된다.** 표본 14건 중 3건(21%)이 어긋났다.
        "SELECT COUNT(*) FROM 표결집계 a WHERE a.재적수 <>"
        " (SELECT COUNT(*) FROM 표결 v WHERE v.의안번호=a.의안번호)",
    ),
    ("R11", "의원코드를 못 푼 발언자가 있는 회의", "SELECT COUNT(*) FROM 수집실패 WHERE 대상종류='발언의원'"),
    (
        "R12",
        "회의의안이 0건인 (국정감사 아닌) 회의",
        # 업무보고·현안질의만 한 회의가 실재하므로 게이트가 못 된다.
        # **급증하면 SUB_NAME 파서가 통째로 죽은 것이다** — V16 은 표본 3건만 본다.
        "SELECT COUNT(*) FROM 회의 m WHERE m.회의종류 <> '국정감사'"
        " AND NOT EXISTS (SELECT 1 FROM 회의의안 x WHERE x.회의id=m.회의id)",
    ),
    (
        "R13",
        "대안반영폐기인데 대안의안번호가 NULL (U1)",
        # 대안 관계가 대량 누락돼도 게이트는 전부 0일 수 있다. 이 값이 유일한 신호다.
        "SELECT COUNT(*) FROM 의안 WHERE 처리결과='대안반영폐기' AND 대안의안번호 IS NULL",
    ),
    (
        "R14",
        "교체 상태 (얼마나 오래 그 상태인가)",
        # A12 는 "지금 건너뛰고 있나"만 준다. **하루 건너뛴 것과 열흘째 건너뛴 것은
        # 다른 사건이다** — 앞은 원천의 딸꾹질이고 뒤는 원천이 바뀐 것이다.
        # ⚠️ **`갱신일시` 를 기준으로 재지 마라 — 수집이 멈추면 그 값도 같이 멈춘다.**
        #    verify 가 빨갛거나 락에 막혀 수집이 아예 안 도는 동안 이 값은 계속 `0일` 이라고
        #    답하는데 표는 실제로 며칠씩 낡는다. **낡음을 재는 값이 낡음에 가려진다.**
        """
        SELECT COALESCE(GROUP_CONCAT(v, ' · '), '아직 안 돌았다') FROM (
          SELECT 대상 || ' ' || 상태 || ' ' ||
                 CAST(ROUND(julianday('now','localtime')
                            - julianday(COALESCE(상태시작일시, 갱신일시))) AS INTEGER)
                 || '일' AS v
          FROM 수집상태 ORDER BY 대상)
        """,
    ),
    (
        "R15",
        "직전 정상(백업 세대) 대비",
        # A13 은 수만 준다. **어느 표가 얼마나 줄었는지**가 없으면 사람이 빨간불 앞에서
        # 할 수 있는 일이 없고, "기준선 없음" 인지도 여기서만 보인다.
        _급감상세,
    ),
    # A14 는 수만 준다. **어느 뷰가 어떻게 깨졌는지**는 여기서만 보인다.
    ("R16", "SCHEMA 의 뷰 (조회되고 비지 않는가)", lambda conn: (
        lambda 전부, 난것: f"{전부 - len(난것)}/{전부} 정상"
        + (" · " + " · ".join(난것) if 난것 else ""))(*_깨진뷰상세(conn))),
    # 원천의 상태이지 우리가 고칠 수 있는 것이 아니므로 게이트가 아니라 보고값이다.
    ("R17", "정당확인일이 7일 넘게 지났거나 없는 현직",
     "SELECT COUNT(*) FROM 의원 WHERE 현직여부=1 AND (정당확인일 IS NULL OR julianday('now','localtime') - julianday(정당확인일) > 7)"),
    ("R18", "상위 없는 소위 이름",
     "SELECT COUNT(*) FROM 위원회 WHERE 상위위원회 IS NULL AND 위원회명 LIKE '%소위%'"),
]


def run(conn) -> tuple[list, list, int]:
    """⚠️ **세 번째 자리는 SQL 이거나 `(conn) -> 값` 이다.** 축 하나를 넣으려고 게이트
    열셋을 고치는 대신 자리 하나의 타입을 넓혔다 — DB 밖을 봐야 하는 판정은 SQL 로 못 쓴다.

    ⚠️ **질의가 깨지는 것 자체가 위반이다.** 표나 컬럼이 사라지면 여기서 예외가 나는데,
    그냥 두면 **감사가 통째로 죽어서 나머지 게이트의 답도 못 듣는다** — 하나가 깨진 것과
    전부를 모르는 것은 다르다. 감사는 일부러 `init_schema` 를 안 부르므로(감사가 DB 를
    고치면 감사가 아니다) 이행 전 DB 와 옛 백업 세대에서 실제로 이 길로 온다.
    """
    _직전캐시.pop(id(conn), None)      # 같은 연결로 다시 부르면 다시 잰다
    결과게이트, 결과보고, 실패 = [], [], 0
    for 번호, 이름, sql in 게이트:
        try:
            v = sql(conn) if callable(sql) else (conn.execute(sql).fetchone()[0] or 0)
        except sqlite3.Error as e:
            결과게이트.append((번호, f"{이름} · 🔴 질의가 깨졌다: {e}", 1, False))
            실패 += 1
            continue
        실패 += v != 0
        결과게이트.append((번호, 이름, v, v == 0))
    for 번호, 이름, sql in 보고:
        try:
            결과보고.append((번호, 이름, sql(conn) if callable(sql) else conn.execute(sql).fetchone()[0]))
        except sqlite3.Error as e:
            결과보고.append((번호, 이름, f"🔴 질의가 깨졌다: {e}"))
    return 결과게이트, 결과보고, 실패


_해석 = {
    "A0": "SCHEMA 를 고쳐 놓고 DB 에 반영을 안 했다 — `db.py migrate` 를 돌려라."
          " 그때까지 `.schema` 는 옛 주석을 내놓는데 아무도 그렇다고 말해 주지 않는다.",
    "A4": "원천은 주는데 우리가 저장할 수 없다 — 사람이 고칠 때까지 계속 빨갛다.",
    "A11": "재시도 상한에 닿아 포기했다 — 그 항목은 영영 안 받아진다."
           " 원천 결함으로 확인했으면 `collect.py --보류 <대상종류> <대상키> --이유 …` 로"
           " 표시한다 — 그러면 여기서 빠지고 R6 에 남는다.",
    "A12": "**사람이 손대서 푸는 빨간불이 아니다.** 원천이 줄어 교체를 건너뛴 것이고,"
           " 원천이 돌아오면 저절로 풀린다. 다만 **그동안 그 표는 옛 값을 그대로 들고"
           " 있어** 위원회 배정·현직 여부를 물으면 에러 없이 어제 답이 나온다."
           " 며칠째인지는 R14 가 준다 — 하루면 원천의 딸꾹질, 열흘이면 원천이 바뀐 것이다.",
    "A13": "직전 정상(백업 세대)보다 핵심표가 크게 줄었다 — 어느 표인지는 R15."
           " 다른 게이트는 전부 현재 DB **안**의 등식이라 행이 사라져도 초록이다.",
    "A14": "뷰가 깨졌다 — 어느 뷰인지는 R16. 뷰가 대신 지키던 함정이 통째로 되살아난다:"
           " `법률안제안주체` 없이 발의자를 조인하면 가결 법안의 절반이 사라지고,"
           " `의안회의` 없이 위원장 대안을 물으면 논의가 0건이 된다.",
}


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    a = ap.parse_args()

    conn = db.connect(a.db)
    try:
        게, 보, 실패 = run(conn)
        print(f"\n{'#':<5} {'게이트 (0이어야 한다)':<44} 값")
        print("─" * 78)
        for 번호, 이름, v, ok in 게:
            print(f"{번호:<5} {이름:<44} {v:>10,}  {'🟢' if ok else '🔴'}")
        print(f"\n{'#':<5} {'보고값 (추이를 본다)':<44} 값")
        print("─" * 78)
        for 번호, 이름, v in 보:
            print(f"{번호:<5} {이름:<44} {v}")
        print("─" * 78)
        if 실패:
            print(f"\n🔴 게이트 {실패}건 위반. **전량 수집으로 넘어가지 마라.**")
            # ⚠️ **해석을 여기 두는 이유.** 게이트 이름은 "무엇을 셌나"이지 "그래서 무슨
            #    일이 났나"가 아니다. 그 해석을 지면(SKILL.md)에 적으면 게이트를 고친 날
            #    지면만 낡고, 정작 빨간불을 보는 사람은 지면을 안 연다 — 이 줄은 빨간
            #    순간에만, 공짜로 읽힌다.
            for 번호, 말 in _해석.items():
                if any(n == 번호 and not ok for n, _, _, ok in 게):
                    print(f"   {번호}: {말}")
        else:
            print(f"\n🟢 게이트 {len(게)}건 전부 통과.")
        return 1 if 실패 else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main())
