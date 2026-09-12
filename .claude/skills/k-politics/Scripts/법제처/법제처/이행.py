"""LAW.db 의 스키마 적용과 이행 — `init_schema`·`migrate`·`이행`, 그리고 `적재.py` 의 CLI.

`이행()` 은 `스키마버전` 이 목표에 못 미치면 그 차이만큼 옮긴다. **멱등이다.**

⚠️ **`init_schema()` 다음, `migrate()` 앞에 부른다.** 앞이어야 새 컬럼·새 테이블이 이미 있고, 뒤여야 구조를 다 옮긴 상태에서 주석이 갈아 끼워진다. 순서가 뒤집히면 `migrate()` 가 구조불일치로 물러나고 **주석이 DB 안에서 낡은 채로 남는다.**

⚠️ **주석으로 백업을 요구하던 자리다. 이제 문이 대신 선다** — `백업확인=True` 면 신선한 검증본이 없을 때 아예 시작하지 않는다. 컬럼 DROP 과 테이블 재구축은 되돌릴 수 없고, 테이블 단위 트랜잭션은 있지만 상위 트랜잭션이 없어 중간에 끊기면 앞서 지운 컬럼은 돌아오지 않는다. 되돌릴 수단을 쥐기 전에 시작하지 않는 것이 유일한 방어다.

⚠️ **문은 옮길 것이 실제로 있을 때만 선다.** 이행은 멱등이라 평소에는 곧바로 물러나는데, 그 no-op 앞에서도 백업을 요구하면 **백업이 하루 밀린 날 수집 전체가 멈춘다.**
"""

from __future__ import annotations


import os
import re
import sqlite3
import sys
from .스키마 import SCHEMA, _구조, _저장형, _테이블문, 드리프트질의
from .연결 import SKILL_DIR, connect, db_path, now_kst, 락, 트랜잭션
from .저장 import 메타쓰기, 메타읽기
from .정규화 import 날짜, 날짜컬럼, 법원명정규화, 선고정규화

def 컬럼보강(conn: sqlite3.Connection, schema: str | None = None) -> list[str]:
    """`SCHEMA` 에 새로 생긴 컬럼을 기존 DB 에 붙인다. **덧붙이기만 한다.**

    `CREATE TABLE IF NOT EXISTS` 는 테이블이 있으면 통째로 건너뛰므로, 이게 없으면 컬럼을
    하나 더한 날 **다음 수집이 `no such column` 으로 죽는다.** 두 시간 반짜리 수집물을 놓고
    사람이 손으로 `ALTER` 를 치게 만드는 것은, 자동으로 도는 파이프라인에서는 그냥 고장이다.

    ⚠️ **없어진 컬럼은 지우지 않고 이름만 돌려준다.** 삭제는 되돌릴 수 없으니 사람이 판단한다.
    ⚠️ `ALTER TABLE ADD COLUMN` 은 **끝에만** 붙는다. 그래서 `SCHEMA` 에서도 새 컬럼은
       맨 뒤여야 `migrate()` 의 주석 교체가 산다(구조가 어긋나면 물러난다).
    """
    붙임: list[str] = []
    for m in _테이블문.finditer(SCHEMA if schema is None else schema):
        이름 = m.group(1)
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (이름,)
        ).fetchone():
            continue
        산것 = [r[1] for r in conn.execute(f"PRAGMA table_info({이름})")]
        for 줄 in re.findall(r"^\s{2}(\S+)\s+(TEXT|INTEGER|REAL|BLOB)\b([^\n]*)",
                             m.group(0), re.M):
            컬럼, 타입, 꼬리 = 줄
            if 컬럼 in 산것:
                continue
            # ⚠️ **줄 끝 주석을 먼저 떼어낸다.** 이 DB 는 주석이 곧 문서라 컬럼 옆에
            #    `-- …` 이 붙는 것이 통상인데, 그대로 두면 쉼표가 문장 한복판에 남아
            #    `ALTER TABLE … TEXT, -- 설명` 이 되고 `near ",": syntax error` 로 죽는다.
            #    **새 컬럼에만 일어나므로 평소에는 초록이고, 컬럼을 더한 날 수집이 죽는다.**
            꼬리 = 꼬리.split("--")[0]
            # NOT NULL 을 기본값 없이 붙이면 SQLite 가 거부한다. 그건 사람이 판단할 일이다.
            if "NOT NULL" in 꼬리 and "DEFAULT" not in 꼬리:
                raise RuntimeError(f"{이름}.{컬럼} 은 기본값 없는 NOT NULL 이라 자동으로 못 붙인다")
            conn.execute(f"ALTER TABLE {이름} ADD COLUMN {컬럼} {타입}{꼬리.rstrip().rstrip(',')}")
            붙임.append(f"{이름}.{컬럼}")
    return 붙임


def _문장들(script: str) -> list[str]:
    """스크립트를 문장 단위로 자른다. **문자열·주석 안의 `;` 는 안 건드린다.**

    `sqlite3.complete_statement()` 가 SQLite 자신의 판정이라 직접 `;` 로 쪼개는 것과 다르다
    — 주석에 든 세미콜론이나 안 닫힌 따옴표를 우리가 다시 판정하지 않는다.
    """
    조각, 모음 = "", []
    for 줄 in script.splitlines(keepends=True):
        조각 += 줄
        if sqlite3.complete_statement(조각):
            모음.append(조각)
            조각 = ""
    if 조각.strip():
        모음.append(조각)
    return 모음


def _PRAGMA인가(문: str) -> bool:
    """앞의 빈 줄·`--` 주석을 지나 첫 낱말이 PRAGMA 인가."""
    for 줄 in 문.splitlines():
        줄 = 줄.strip()
        if 줄 and not 줄.startswith("--"):
            return 줄.upper().startswith("PRAGMA")
    return False


def _스키마적용(conn: sqlite3.Connection, script: str) -> None:
    """`SCHEMA` 를 **한 트랜잭션으로** 적용한다.

    ⚠️ **`executescript()` 를 쓰면 안 된다.** 문장 하나하나가 따로 커밋되므로,
    뷰의 `DROP VIEW IF EXISTS` → `CREATE VIEW` 쌍 사이에서 무엇이든 실패하면
    **뷰가 없는 DB 가 확정 커밋된다.** 1밀리초짜리 경합이 아니라 뒤쪽 문장 하나가
    깨지기만 해도 그렇게 되고, 그 상태는 조회에 `no such table` 로 나타나 다음 수집이
    돌 때까지 — 하루 — 이어진다.

    ⚠️ 감싸는 것만으로는 안 된다 — `executescript()` 는 **열린 트랜잭션도 암묵적으로
    커밋한다.** 그래서 문장 단위로 나눠 넣는다.

    ⚠️ **PRAGMA 는 트랜잭션 밖이다.** `journal_mode` 는 트랜잭션 안에서 바뀌지 않고,
    안에서 조용히 무시되면 **파일 속성이 안 바뀐 채로 초록**이 된다.
    """
    문장들 = _문장들(script)
    for 문 in 문장들:
        if _PRAGMA인가(문):
            conn.execute(문)
    with 트랜잭션(conn):
        # ⚠ 새 컬럼을 참조하는 인덱스·뷰보다 먼저 붙여야 옛 DB 에도 적용된다.
        컬럼보강(conn, script)
        for 문 in 문장들:
            if not _PRAGMA인가(문):
                conn.execute(문)


def init_schema(conn: sqlite3.Connection) -> None:
    _스키마적용(conn, SCHEMA)
    conn.execute(
        "INSERT INTO 메타 (키, 값) VALUES ('스키마버전', ?)"
        " ON CONFLICT(키) DO NOTHING",
        (스키마버전,),
    )


스키마버전 = "3"


# 파서를 고치면 이 값을 올린다. `수집.py --재파싱` 이 메타의 값과 비교해 전량 재파싱한다.
파서버전 = "1"


# ═══════════════════════════════════════════════════════════
# 쓰기 — 자료종류별 삽입을 여기 한 곳에 모은다
# ═══════════════════════════════════════════════════════════
#
# **나중에 FTS 가 필요해지면 고칠 곳이 여기 하나여야 한다**(D14 각주). 지금 안 만드는
# 것이지 영원히 안 만드는 것이 아니다.

# 자료종류 → (본문 테이블, 기본키 컬럼, [(자식 테이블, 부모키 컬럼)] 삽입 순서)


def migrate(conn: sqlite3.Connection, schema: str | None = None, *,
            백업확인: bool = True) -> dict[str, list[str]]:
    """주석만 바뀐 테이블의 DDL 텍스트를 제자리에서 갈아 끼운다.

    `CREATE TABLE IF NOT EXISTS` 는 테이블이 이미 있으면 문장을 통째로 건너뛴다 —
    **기존 DB 에 주석 변경이 반영되지 않는다.** 이 DB 는 주석이 곧 문서라
    "주석만 고치는 변경"이 흔하므로 그 길이 필요하다.

    ⚠️ **구조가 다르면 손대지 않고 이름만 돌려준다.** `writable_schema` 는 검증 없이
    카탈로그를 갈아 끼우므로, 구조가 다른 DDL 을 넣으면 **테이블과 카탈로그가 어긋난 채로
    열린다.** 그 상태는 조용하다가 나중에 이상하게 터진다. 컬럼 추가·삭제·타입 변경은
    사람이 `ALTER TABLE` 로 하고 나서 이 함수를 부른다.

    ⚠️ **부르기 전에 DB 를 복사해 둬라.** `writable_schema` 로 망가진 DB 는 되돌릴 방법이 없다.
    """
    schema = SCHEMA if schema is None else schema
    결과: dict[str, list[str]] = {"주석교체": [], "구조불일치": []}
    바꿀것: list[tuple[str, str]] = []

    for m in _테이블문.finditer(schema):
        이름, 새 = m.group(1), _저장형(m.group(0))
        옛row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (이름,)
        ).fetchone()
        if 옛row is None or 옛row[0] == 새:
            continue
        if _구조(옛row[0]) != _구조(새):
            결과["구조불일치"].append(이름)
            continue
        결과["주석교체"].append(이름)
        바꿀것.append((새, 이름))

    if not 바꿀것:
        return 결과

    # ⚠️ **여기가 되돌릴 수 없는 지점이다.** `writable_schema` 로 카탈로그를 직접 고치므로
    #    잘못되면 되돌릴 방법이 없다 — 이 함수 주석이 처음부터 "부르기 전에 DB 를 복사해
    #    둬라"고 요구해 온 이유다. **주석 대신 문이 선다.**
    #    문이 `if not 바꿀것` **뒤에** 있는 것이 중요하다: 이 함수는 매 수집이 부르는데
    #    바꿀 것이 없으면 아무것도 안 하므로, 앞에 두면 **백업이 하루 밀린 날 수집 전체가
    #    멈춘다.** 문은 실제로 고칠 것이 있을 때만 선다.
    if 백업확인:
        _백업이_있어야_한다(conn)

    if (before := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
        raise RuntimeError(f"migrate 전부터 무결성이 깨져 있다: {before}")

    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("BEGIN")
    try:
        conn.execute("PRAGMA writable_schema=ON")
        conn.executemany(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?", 바꿀것
        )
        # 카탈로그를 손으로 고쳤다는 것을 다른 연결이 알아채게 한다. 안 올리면 이미 열려 있는
        # 연결이 옛 스키마를 캐시한 채로 돈다.
        conn.execute(f"PRAGMA schema_version={version + 1}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA writable_schema=OFF")

    if (after := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
        raise RuntimeError(f"migrate 뒤 무결성이 깨졌다: {after}")
    if 위반 := conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError(f"migrate 뒤 FK 위반: {위반}")
    return 결과


# ── 이행 ─────────────────────────────────────────────────────
#
# `migrate()` 가 **주석**을 갈아 끼운다면 이쪽은 **구조와 값**을 옮긴다. 둘을 나눈 것은
# 위험이 다르기 때문이다 — 주석 교체는 매 실행 도는 무해한 일이고, 이행은 되돌릴 수 없다.


def 스키마출력(explicit: str | os.PathLike[str] | None = None) -> tuple[str, str | None]:
    """`.schema` 로 볼 것과, 그것을 믿어도 되는지에 대한 경고를 함께 돌려준다.

    **DB 가 있으면 DB 것을 낸다.** 조회하는 쪽이 실제로 읽는 것이 `sqlite_master` 라,
    상수를 내면 "내가 본 스키마"와 "클로드가 보는 스키마"가 조용히 갈린다.

    ⚠️ **경고는 stdout 이 아니라 stderr 로 나가야 한다.** 이 명령의 출력은 그대로 읽히는
    스키마 본문이라, 경고 한 줄이 stdout 에 섞이면 그 줄이 스키마의 일부로 읽힌다.
    """
    path = db_path(explicit)
    if not path.exists():
        return SCHEMA.strip(), f"⚠️ {path} 가 없어 **상수**를 냈다. 실물 DB 의 것이 아니다."
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        본문 = "\n".join(
            r[0] + ";"
            for r in conn.execute(
                "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"
            )
        )
        갈림 = conn.execute(드리프트질의).fetchone()[0]
    finally:
        conn.close()
    경고 = None
    if 갈림:
        경고 = (
            f"⚠️ 테이블 {갈림}개가 상수와 다르다 — **DB 쪽이 낡았다.**"
            " `migrate` 를 돌려야 위 주석이 상수를 따라잡는다."
        )
    return 본문, 경고


def 이행(conn: sqlite3.Connection, 로그=lambda _: None, *,
         백업확인: bool = True) -> dict[str, int]:
    # ⚠️ **문자열로 비교하지 마라.** `"9" >= "10"` 이 참이라 **버전 10에서 이행을 통째로
    #    건너뛴다** — 아무 에러 없이, 구조만 낡은 채로. 버전이 한 자리를 넘는 날 딱 한 번
    #    조용히 터지는 종류라 그날 원인을 찾을 단서가 없다. 저장은 문자열이지만 비교는 수다.
    # 성진: 선고 보강은 원문 NULL을 표지로 버전과 별개로 재개한다, 전체 판례 탐색이 병목이면 완료 메타와 재수집 경로를 함께 설계한다.
    현재 = int(메타읽기(conn, "스키마버전") or "1")
    목표 = int(스키마버전)
    선고대상 = conn.execute(
        "SELECT EXISTS (SELECT 1 FROM 판례 WHERE 선고원문 IS NULL AND 선고 IS NOT NULL)"
    ).fetchone()[0]
    if 현재 >= 목표 and not 선고대상:
        return {}

    if 백업확인:
        _백업이_있어야_한다(conn)

    수치: dict[str, int] = {}
    if 현재 < 2:
        수치 |= _이행_2(conn, 로그)
    if 현재 < 3:
        수치 |= _이행_3(conn, 로그)
    if 선고대상:
        수치["선고"] = _선고재작성(conn, 로그)
    if 현재 < 목표:
        메타쓰기(conn, "스키마버전", 스키마버전)
    return 수치


def _백업모듈():
    """`Scripts/백업.py`. **판정 규칙을 여기 옮겨 적지 않는다** — 두 곳에 적으면 한쪽만
    고치게 되고, 그 순간 문이 열린 채로 닫혀 있다고 믿게 된다.

    임포트가 함수 안에 있는 이유: `백업.py` 가 거꾸로 이 모듈을 불러 `db_path` 를 쓰므로,
    모듈 최상단에서 서로를 부르면 순환이 된다. 양쪽 다 **쓸 때** 부르면 순환이 아니다.
    """
    import importlib.util

    if (있음 := sys.modules.get("백업")) is not None:
        return 있음
    경로 = SKILL_DIR / "Scripts" / "백업.py"
    if not 경로.is_file():
        raise SystemExit(
            f"🔴 거부한다 — 백업 도구를 찾지 못했다: {경로}\n"
            "   이행·migrate 는 24시간 이내 검증된 백업이 있어야 시작한다. 이 레포에선 스킬 폴더에서"
            " `uv run Scripts/백업.py law --이행직전`. 스킬 폴더만 복사한 환경이면 그 도구가 없다 —"
            " `수집.py --help` 의 설치 시나리오 ④."
        )
    spec = importlib.util.spec_from_file_location("백업", 경로)
    m = importlib.util.module_from_spec(spec)
    sys.modules["백업"] = m
    spec.loader.exec_module(m)
    return m


def _백업이_있어야_한다(conn: sqlite3.Connection) -> None:
    """**연결이 실제로 연 파일**을 물어 그 파일의 백업을 요구한다. 환경변수나 인자가
    아니라 연결에 묻는 이유는, 문이 지키는 대상과 고쳐지는 대상이 갈릴 수 없게 하려는 것이다."""
    m = _백업모듈()
    m.이행전_확인("law", m.연파일(conn))


# ⚠️ **지우는 이유가 '중복'이 아니라 '매 세션 읽는 지면'이다.** 이 DB 는 스키마 주석이
#    곧 문서라 `.schema` 가 클로드의 컨텍스트에 매번 들어간다. 아래는 전부 **조건으로 걸
#    것이 없는** 컬럼이다 — 상수(해석기관명 8,804행 전부 '법제처'), 파생(법원종류코드는
#    법원명의 함수), 사실상 빔(처분청 35,045행 중 5행), 해독표 없는 코드(편장절관).
#    필요해지면 원천에 그대로 있으니 다시 받는다.
_지울컬럼: tuple[tuple[str, str], ...] = (
    ("법령", "법령명한자"), ("법령", "편장절관"), ("법령", "전화번호"), ("법령", "법종구분코드"),
    ("판례", "법원종류코드"),
    ("행정심판례", "처분청"), ("행정심판례", "처분일자"),
    ("법령해석례", "해석기관명"), ("법령해석례", "해석기관코드"),
)


# ⚠️ **이 셋은 `ALTER TABLE DROP COLUMN` 으로 못 지운다.** `id INTEGER PRIMARY KEY` 라
#    SQLite 가 `cannot drop PRIMARY KEY column` 으로 거부한다. 통째로 다시 만드는 수밖에 없다.
_재구축할테이블: tuple[str, ...] = ("의율조문", "인용판례", "파싱실패")


# ⚠️ **D31 과 같은 기준을 남은 컬럼에 다시 적용한 결과다**(D38) — 지우는 이유가 '중복'이
#    아니라 매 세션 읽는 지면이다. 여섯 다 파생이거나(이름과 완전 1:1), 읽을 표가 없는
#    코드이거나, 절반 넘게 비어 있다. 근거 수치는 각 컬럼이 있던 자리의 주석에 남겼다.
#
# ⚠️ `법령.소관부처코드` 는 여기 없다 — `법령` 은 `CHECK` 때문에 어차피 재구축하므로
#    그 재구축이 함께 떨어뜨린다. 여기 또 적으면 인덱스(`idx_법령_부처`)가 걸려 있어
#    `DROP COLUMN` 이 거부당한다.
_지울컬럼3: tuple[tuple[str, str], ...] = (
    ("판례", "사건종류코드"),
    ("헌재결정례", "사건종류코드"), ("헌재결정례", "재판부구분코드"),
    ("행정심판례", "재결례유형코드"),
    ("법령해석례", "질의기관코드"),
    ("행정규칙", "행정규칙종류코드"),
)


# `CHECK` 을 붙이려고 다시 만든다. 앞의 둘은 FK 부모라 `_테이블재구축` 의 PRAGMA 방어가
# 처음으로 실제 일을 하는 자리다.
_재구축할테이블3: tuple[str, ...] = ("법령", "조문", "의율조문", "인용판례")


def _이행_2(conn: sqlite3.Connection, 로그) -> dict[str, int]:
    # ⚠️ **법원명 정규화가 `법원종류코드` 삭제보다 먼저다.** 지금 심급을 가릴 수단이
    #    그 코드뿐인데, 이름이 224종으로 흔들려서다. 정규화가 들어와야 `법원명` 이 그
    #    일을 대신한다. 순서가 뒤집히면 그 사이에 심급 필터가 사라진다.
    수치 = {"날짜": _날짜재작성(conn, 로그), "법원명": _법원명재작성(conn, 로그)}
    수치["컬럼삭제"] = _컬럼삭제(conn, 로그, _지울컬럼)
    수치["재구축"] = _테이블재구축(conn, 로그, _재구축할테이블)
    return 수치


def _이행_3(conn: sqlite3.Connection, 로그) -> dict[str, int]:
    """컬럼 7 · 테이블 1 을 지우고 `CHECK` 4 를 붙인다(D38·D41·D42).

    ⚠️ **뷰는 여기서 만들지 않는다.** `init_schema()` 가
    이미 만들었고, 재구축이 인덱스를 되살리며 한 번 더 만든다.

    ⚠️ **그래서 뷰가 지울 컬럼을 참조하면 안 된다** — `현행법령`·`현행조문` 은 하나도
    안 쓴다. 이 환경에서 뒤탈 없이 지나가는 것이 운이 아니라는 것을 실측으로 확인했다:
    `legacy_alter_table` 기본값이 **연결마다 다르다.** Python 의 sqlite3(3.45.3)은 0 이라
    뷰가 참조하는 컬럼의 `DROP COLUMN` 을 **거부하고 원상태를 지키는데**, 이 기기의
    `sqlite3` CLI(3.51.0)는 1 이라 **DROP 이 성공하고 뷰만 깨진다.** 운영 경로는 Python
    이지만 사람이 CLI 로 같은 일을 하면 결과가 다르므로, 어느 쪽이든 안전하도록 뷰가
    지울 컬럼을 안 건드리게 두는 것이 실제 방어다. 감사 A24 가 매 실행 뷰를 조회해
    지킨다 — **깨진 뷰는 조회할 때야 터지고 `integrity_check` 는 통과한다.**
    """
    수치 = {"테이블삭제": _행정규칙별표삭제(conn, 로그)}
    수치["컬럼삭제"] = _컬럼삭제(conn, 로그, _지울컬럼3)
    수치["재구축"] = _테이블재구축(conn, 로그, _재구축할테이블3)
    _수집시각메타(conn, 로그)
    _공간회수(conn, 로그)
    return 수치


def _행정규칙별표삭제(conn: sqlite3.Connection, 로그) -> int:
    """75,384행 · 저장 620MiB(본체 619 + PK 인덱스 2). **크기가 이유가 아니라 닿는 경로가 이유다**(D42).

    서식류(별지·서식·양식)가 60,394행이고 그 대부분이 빈 양식 표지다. 별표를 가진
    행정규칙 13,183개 중 위임으로 법령에서 닿는 것은 2,097개(16%)뿐이었다.
    실물이 필요하면 `직결.py 행정규칙별표`(원천 `admbyl`)로 나간다.
    """
    if not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='행정규칙별표'"
    ).fetchone():
        return 0  # 이미 없다 — 멱등
    n = conn.execute("SELECT COUNT(*) FROM 행정규칙별표").fetchone()[0]
    conn.execute("DROP TABLE 행정규칙별표")
    로그(f"  테이블 삭제 행정규칙별표 {n:,}행")
    return 1


def _수집시각메타(conn: sqlite3.Connection, 로그) -> None:
    """`메타` 주석이 약속하던 두 키를 실물로 채운다.

    ⚠️ **`최초수집일시` 는 데이터에서만 나온다.** 원천은 "우리가 언제 받았나"를 모르므로
    한 번 놓치면 되찾을 방법이 없다. 여섯 테이블을 훑는 값비싼 질의(실측 5초)라
    여기서 한 번만 하고, 이후에는 `수집끝냈다()` 가 싼 쪽만 갱신한다.
    """
    표 = ("법령", "판례", "헌재결정례", "행정심판례", "법령해석례", "행정규칙")
    최초 = min(
        (v for t in 표
         if (v := conn.execute(f"SELECT MIN(수집일시) FROM {t}").fetchone()[0])),
        default=None,
    )
    if 최초:
        conn.execute(
            "INSERT INTO 메타 (키, 값) VALUES ('최초수집일시', ?)"
            " ON CONFLICT(키) DO NOTHING", (최초,)
        )
        로그(f"  최초수집일시 {최초}")
    메타쓰기(conn, "마지막수집일시", 메타읽기(conn, "마지막수집일시") or now_kst())


def _선고재작성(conn: sqlite3.Connection, 로그) -> int:
    conn.create_function("_선고", 1, 선고정규화, deterministic=True)
    with 트랜잭션(conn):
        바뀜 = conn.execute(
            "UPDATE 판례 SET 선고원문=선고, 선고=_선고(선고)"
            " WHERE 선고원문 IS NULL AND 선고 IS NOT NULL"
        ).rowcount
    if 바뀜:
        로그(f"  선고 원문 보존·정규화 {바뀜:,}행")
    return 바뀜


def _법원명재작성(conn: sqlite3.Connection, 로그) -> int:
    """원천 표기를 `법원명원문` 으로 챙긴 **다음** `법원명` 을 정규화한다.

    ⚠️ **순서가 뒤집히면 원천 표기가 영영 사라진다.** `법원명` 을 먼저 덮어쓰면
    `법원명원문` 에 채울 원본이 이미 없다.
    """
    conn.create_function("_법원명", 1, 법원명정규화, deterministic=True)
    with 트랜잭션(conn):
        챙김 = conn.execute(
            "UPDATE 판례 SET 법원명원문=법원명 WHERE 법원명원문 IS NULL"
        ).rowcount
        바뀜 = conn.execute(
            "UPDATE 판례 SET 법원명=_법원명(법원명원문) WHERE 법원명 IS NOT _법원명(법원명원문)"
        ).rowcount
    if 챙김 or 바뀜:
        로그(f"  법원명 원문 보존 {챙김:,}행 · 정규화 {바뀜:,}행")
    return 바뀜


def _공간회수(conn: sqlite3.Connection, 로그) -> None:
    """**`VACUUM` 없이는 회수한 620MiB 가 파일에 그대로 남는다.**

    `DROP TABLE`·`DROP COLUMN`·재구축은 페이지를 free list 에 돌려줄 뿐 파일을 줄이지
    않는다. 이 DB 는 통째로 복사·백업되므로 그 차이가 매번 든다.

    ⚠️ **트랜잭션 안에서는 실행할 수 없다.** `connect()` 가 autocommit 이라 여기서는
    되지만, 이 호출을 `트랜잭션()` 블록 안으로 옮기면 `cannot VACUUM from within a
    transaction` 으로 죽는다.
    ⚠️ **원본만 한 여유 공간이 필요하다.** 모자라면 여기서 터지는데, 그때도 `스키마버전`
    은 아직 안 올라간 상태라 다음 실행이 같은 자리에서 다시 시작한다 — 앞 단계들이
    전부 멱등이라 성립하는 이야기다.
    """
    전 = conn.execute("SELECT page_count * page_size FROM pragma_page_count(),"
                      " pragma_page_size()").fetchone()[0]
    conn.execute("VACUUM")
    후 = conn.execute("SELECT page_count * page_size FROM pragma_page_count(),"
                      " pragma_page_size()").fetchone()[0]
    로그(f"  VACUUM {전 / 2**30:.2f}GB → {후 / 2**30:.2f}GB")


def _컬럼삭제(conn: sqlite3.Connection, 로그, 목록: tuple[tuple[str, str], ...]) -> int:
    """⚠️ **인덱스가 걸린 컬럼은 못 지운다** — SQLite 가 `error in index … after drop
    column` 으로 거부한다. 목록에 든 것이 전부 인덱스 밖이라는 것을 실측으로 확인했다.
    새로 더할 때는 `sqlite_master` 를 먼저 봐라 — 인덱스가 걸렸으면 그 테이블을
    `_테이블재구축` 쪽으로 보내는 것이 낫다(인덱스가 SCHEMA 대로 다시 만들어진다).
    """
    n = 0
    for 테이블, 컬럼 in 목록:
        if 컬럼 not in [r[1] for r in conn.execute(f"PRAGMA table_info({테이블})")]:
            continue  # 이미 없다 — 멱등
        conn.execute(f"ALTER TABLE {테이블} DROP COLUMN {컬럼}")
        로그(f"  컬럼 삭제 {테이블}.{컬럼}")
        n += 1
    return n


_인덱스명 = re.compile(r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", re.I)


def _인덱스이름(ddl: str) -> str:
    m = _인덱스명.search(ddl)
    return m.group(1).strip('"') if m else ddl


def _인덱스문(테이블: str) -> list[str]:
    """`SCHEMA` 안에서 그 테이블에 걸린 `CREATE INDEX` 문장들."""
    return [
        m.group(0)
        for m in re.finditer(r"^CREATE INDEX[^\n]*?;", SCHEMA, re.M)
        if re.search(rf"\bON\s+{re.escape(테이블)}\s*\(", m.group(0))
    ]


def _테이블재구축(conn: sqlite3.Connection, 로그, 테이블들: tuple[str, ...]) -> int:
    """DDL 을 바꾸려고 테이블을 통째로 다시 만든다. **행을 흘리면 안 된다.**

    `ALTER TABLE` 로 못 하는 변경이 대상이다 — PK 컬럼 삭제(`id`)와 `CHECK` 추가.

    ⚠️ **판정은 컬럼 목록이 아니라 구조다.** 컬럼이 그대로인 채 제약만 붙는 변경
    (`CHECK`)이 있어서, 컬럼 차집합으로 물으면 **아무것도 안 하고 조용히 통과한다.**
    `_구조()` 는 주석과 공백만 지우므로 `CHECK` 은 남는다 — 그게 여기 쓰이는 이유다.

    ⚠️ **순서가 전부다.** 옛 테이블을 먼저 지워야 `CREATE INDEX IF NOT EXISTS` 가
    다시 만든다 — `ALTER TABLE RENAME` 은 인덱스를 옛 테이블에 붙인 채 이름만 남기므로,
    안 지운 상태에서 인덱스를 만들면 **이름이 이미 있다고 조용히 건너뛰고** 그 다음
    `DROP TABLE` 에 딸려 사라진다. 의율조문 55만 행의 역방향 집계가 풀스캔이 된다.

    ⚠️ **새 테이블은 `SCHEMA` 의 DDL 로 만든다.** `ALTER TABLE RENAME` 이 남기는
    `CREATE TABLE "이름"` 형태를 쓰면 따옴표 한 쌍 때문에 `migrate()` 가 영원히
    구조불일치로 물러나고, 그러면 이 DB 의 주석(=문서)이 안에서 낡은 채로 남는다.

    ⚠️ **`foreign_keys=OFF` · `legacy_alter_table=ON` 없이 부모 테이블을 재구축하면
    자식의 FK 가 조용히 끊긴다.** SQLite 3.25+ 는 `RENAME` 할 때 **다른 테이블의
    `REFERENCES` 절과 뷰 정의까지 새 이름으로 고쳐 쓴다.** 그래서 `법령` → `_옛_법령`
    이름을 바꾸는 순간 `조문`·`조문요소`·`부칙`·`위임` 이 전부 `_옛_법령` 을 가리키게 되고,
    그 다음 `DROP TABLE _옛_법령` 이 **없는 테이블을 가리키는 FK** 를 남긴다.

    가장 나쁜 것은 그 상태에서 **`PRAGMA foreign_key_check` 가 깨끗하다고 답한다는 것**
    이다(3.45.3 실측) — 없는 테이블에는 걸 제약이 없어서다. 게이트가 거짓말을 하므로
    막는 것 말고 알아낼 방법이 없다. 두 PRAGMA 는 트랜잭션 안에서 무시되므로 밖에서 건다.
    """
    문장 = {m.group(1): m.group(0) for m in _테이블문.finditer(SCHEMA)}
    할것 = [
        t for t in 테이블들
        if (옛 := conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()) and _구조(옛[0]) != _구조(_저장형(문장[t]))
    ]
    if not 할것:
        return 0  # 이미 목표 모양이다 — 멱등

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        for 테이블 in 할것:
            산것 = [r[1] for r in conn.execute(f"PRAGMA table_info({테이블})")]
            목표 = re.findall(r"^\s{2}(\S+)\s+(?:TEXT|INTEGER|REAL|BLOB)\b", 문장[테이블], re.M)
            옮길 = ", ".join(c for c in 목표 if c in 산것)
            # ⚠️ **인덱스 DDL 을 지금 걷어 둔다.** `DROP TABLE` 이 함께 지우는데
            #    `SCHEMA` 만 다시 돌리면 **운영자가 손으로 만든 인덱스는 영영 사라진다.**
            인덱스 = [
                r[0] for r in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index'"
                    " AND tbl_name=? AND sql IS NOT NULL", (테이블,))
            ]
            with 트랜잭션(conn):
                전 = conn.execute(f"SELECT COUNT(*) FROM {테이블}").fetchone()[0]
                conn.execute(f"ALTER TABLE {테이블} RENAME TO _옛_{테이블}")
                # ⚠️ `executescript()` 는 열린 트랜잭션을 **암묵적으로 커밋한다.**
                #    여기서 쓰면 재구축이 원자성을 잃는다 — 한 문장이니 execute 로 충분하다.
                conn.execute(문장[테이블])
                conn.execute(f"INSERT INTO {테이블} ({옮길}) SELECT {옮길} FROM _옛_{테이블}")
                후 = conn.execute(f"SELECT COUNT(*) FROM {테이블}").fetchone()[0]
                if 전 != 후:
                    raise RuntimeError(f"{테이블} 재구축에서 행이 샜다: {전:,} → {후:,}")
                conn.execute(f"DROP TABLE _옛_{테이블}")
                # ⚠️ **같은 트랜잭션 안이어야 한다.** 커밋을 먼저 하고 루프가 끝난 뒤에
                #    되살리면, 그 사이 무엇이든 터졌을 때 **테이블은 확정됐고 인덱스만 없는
                #    반쪽 상태**가 남는다. 다시 돌려도 구조가 같아 아무것도 안 한다.
                #    `SCHEMA` 것이 정본이고, 그 밖의 것(운영자가 손으로 만든 것)은
                #    **최선노력**이다 — 재구축이 지운 컬럼을 가리키면 살릴 수 없다.
                for ddl in _인덱스문(테이블):
                    conn.execute(ddl)
                정본 = {_인덱스이름(d) for d in _인덱스문(테이블)}
                for ddl in (d for d in 인덱스 if _인덱스이름(d) not in 정본):
                    try:
                        conn.execute(ddl)
                    except sqlite3.OperationalError as e:
                        로그(f"  ⚠️ SCHEMA 밖 인덱스를 못 살렸다: {_인덱스이름(ddl)} — {e}")
            로그(f"  재구축 {테이블} {후:,}행")
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")

    # 켠 뒤에 본다 — 자식이 옛 이름을 물고 있으면 여기서 잡히는 것이 아니라 위 PRAGMA 가
    # 그 상황 자체를 막는다. 이건 옮기다 흘린 실제 고아를 잡는 그물이다.
    if 위반 := conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError(f"재구축 뒤 FK 위반: {위반[:20]}")
    return len(할것)


def _날짜재작성(conn: sqlite3.Connection, 로그) -> int:
    """저장된 날짜에 `날짜()` 를 **다시 먹인다.** 포맷 변환이 아니다.

    ⚠️ **문자열을 잘라 하이픈만 끼우면 안 된다.** 적재 코드가 고쳐진 뒤에 들어온 값과
    그 전에 들어온 값이 섞여 있어서, 실측하면 8자리가 아닌 것이 남아 있다 —
    헌재 `'0'` 5,080 · 판례 단기 45 · 행정규칙 6자리 1 · 달력 밖 2건. 하이픈만 끼우면
    `'0'` 은 `'-'` 가 되고 단기 4284년은 그대로 미래에 남는다.

    ⚠️ **그래서 `날짜()` 를 SQLite 함수로 등록해 쓴다.** 적재와 이행이 같은 규칙을 보는
    유일한 방법이고, 규칙을 고치면 다음 이행이 저절로 따라온다. 두 곳에 적으면
    **게이트는 초록인데 값이 다른** 상태가 생긴다.
    """
    conn.create_function("_날짜", 1, 날짜, deterministic=True)
    n = 0
    # ⚠️ **컬럼마다 따로 커밋한다.** 4GB DB 에서 12개 컬럼 77만 행을 한 트랜잭션에 묶으면
    #    WAL 이 수 GB 로 부푼다. 중간에 죽어도 이 함수가 멱등이라 다음 실행이 이어받고,
    #    그 사이의 반쪽 상태는 감사 A19 가 빨갛게 잡는다.
    for 테이블, 컬럼 in 날짜컬럼:
        with 트랜잭션(conn):
            바뀜 = conn.execute(
                f"UPDATE {테이블} SET {컬럼} = _날짜({컬럼})"
                f" WHERE {컬럼} IS NOT _날짜({컬럼})"
            ).rowcount
        if 바뀜:
            로그(f"  날짜 {테이블}.{컬럼} {바뀜:,}행")
        n += 바뀜
    return n


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="LAW.db 의 스키마 — schema(출력) · init(없는 표·컬럼을 만든다) · "
                    "migrate(주석을 갈아 끼우고, 구조가 다른 표는 손대지 않고 알린다). init·migrate 는 락을 잡는다(못 잡으면 rc 3).")
    ap.add_argument("명령", choices=("schema", "init", "migrate"))
    ap.add_argument("--db")
    a = ap.parse_args(argv)

    if a.명령 == "schema":
        본문, 경고 = 스키마출력(a.db)
        print(본문)
        if 경고:
            print(경고, file=sys.stderr)
        return 0

    with 락(db_path(a.db)) as lk:
        if not lk.잡음:
            return 3
        conn = connect(a.db)
        # ⚠ 백업 사본을 `cp` 하면 0444 그대로다 — 컬럼 보강이 `attempt to write a readonly database` 로 죽는다.
        if not os.access(db_path(a.db), os.W_OK):
            print(f"🔴 DB 파일이 읽기 전용이다: {db_path(a.db)} — `chmod u+w` 뒤에 다시 돌려라", file=sys.stderr)
            return 1
        if a.명령 == "init":
            init_schema(conn)
            n = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
            ).fetchone()[0]
            print(f"🟢 스키마 적용. 테이블 {n}개 · {db_path(a.db)}")
            return 0

        r = migrate(conn)
        for t in r["주석교체"]:
            print(f"  주석 교체: {t}")
        for t in r["구조불일치"]:
            print(f"🔴 구조가 다르다 (손대지 않았다): {t}")
        if not r["주석교체"] and not r["구조불일치"]:
            print("🟢 바꿀 것이 없다.")
        return 1 if r["구조불일치"] else 0

