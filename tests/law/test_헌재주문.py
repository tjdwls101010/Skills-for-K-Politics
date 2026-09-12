"""헌재 결정의 주문(결과)을 전문에서 뽑는다.

원천에 결과 필드가 **없다**(본문 키 13개 확인). 그래서 `전문` 을 파싱하는 것이 유일한
길인데, 마커가 한 종류가 아니다 — 실측 33,395건에서 반각 `[주 문]` 44.9% · 전각
`【주 문】` 41.2% 다. **문서가 "대괄호뿐"이라 단정한 것이 틀렸고**, 반각만 찾으면
혼인빙자간음죄·낙태죄 같은 가장 유명한 위헌 결정을 통째로 놓친다.

여기 원문은 전부 DB 에서 그대로 꺼냈다.
"""

from __future__ import annotations

import re

import pytest

스키마 = pytest.importorskip("law.schema")
심판례 = pytest.importorskip("law.collectors.ruling")
연결 = pytest.importorskip("law.conn")
이행 = pytest.importorskip("law.migrate")


def test_반각_대괄호() -> None:
    assert 심판례.주문추출("[주 문] 청구인의 심판청구를 각하한다. [이 유] 1. 사건의 개요") \
        == "청구인의 심판청구를 각하한다."


def test_전각_대괄호() -> None:
    """실측 41.2% 가 이것이다. 반각만 보면 이 절반이 통째로 안 보인다."""
    assert 심판례.주문추출("【주 문】‘특별법’ 제5조는 헌법에 위반된다.【이 유】가. 쟁점") \
        == "‘특별법’ 제5조는 헌법에 위반된다."


def test_전각_공백이_들어간다() -> None:
    """`【주     문】` — U+3000 과 일반 공백이 섞여 들어온다."""
    글 = "1993. 11. 25. 90헌마17【주　　　문】　　청구인의 이 사건 심판청구를 각하한다.【이　　　유】　1. 사건의 개요"
    assert 심판례.주문추출(글) == "청구인의 이 사건 심판청구를 각하한다."


def test_괄호가_아예_없는_옛_포맷() -> None:
    """1990~2000년대 초 결정문에는 대괄호가 없다."""
    글 = "사      건      2009헌마246  위헌확인\n주      문\n이 사건 심판청구를 각하한다.\n이      유\n1. 사건개요"
    assert 심판례.주문추출(글) == "이 사건 심판청구를 각하한다."


def test_이유_표지가_없으면_앞부분만_받는다() -> None:
    """⚠️ 끝을 못 찾았다고 전문 전체를 주문으로 담으면 안 된다 — `주문 LIKE '%위반된다%'`
    가 이유 속 인용까지 걸어 **합헌 결정이 위헌으로 잡힌다.**"""
    r = 심판례.주문추출("【주 문】 이 사건 신청을 각하한다. " + "가" * 2000)
    assert r is not None and len(r) <= 500


def test_주문_표지가_없으면_비운다() -> None:
    """실측 3건. 없는 것을 지어내지 않는다."""
    assert 심판례.주문추출("재판관김양균________________________재판관황도연") is None
    assert 심판례.주문추출("") is None
    assert 심판례.주문추출(None) is None


def test_주문에_이유가_안_섞인다() -> None:
    """가장 중요한 성질이다. 주문은 결과만 담아야 `LIKE '%위반된다%'` 가 신뢰된다."""
    글 = ("【주 문】 형법 제241조는 헌법에 위반된다."
          "【이 유】 1. 사건개요 … 청구인은 이 조항이 헌법에 위반된다고 주장한다.")
    r = 심판례.주문추출(글)
    assert r == "형법 제241조는 헌법에 위반된다."
    assert "청구인" not in r and "사건개요" not in r


# ── 컬럼보강 ────────────────────────────────────────────────




def _빈DB():
    import sqlite3
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(스키마.SCHEMA)
    return conn


def test_새_컬럼이_기존_DB_에_자동으로_붙는다() -> None:
    """⚠️ `CREATE TABLE IF NOT EXISTS` 는 테이블이 있으면 통째로 건너뛴다. 이게 없으면
    컬럼을 하나 더한 날 **다음 수집이 `no such column` 으로 죽는다.**"""
    conn = _빈DB()
    conn.execute("ALTER TABLE 헌재결정례 DROP COLUMN 주문")
    assert "주문" not in [r[1] for r in conn.execute("PRAGMA table_info(헌재결정례)")]
    assert 이행.컬럼보강(conn) == ["헌재결정례.주문"]
    assert "주문" in [r[1] for r in conn.execute("PRAGMA table_info(헌재결정례)")]


def test_줄_끝_주석이_붙은_컬럼도_붙는다() -> None:
    """⚠️ **이 DB 는 주석이 곧 문서라 컬럼 옆에 `-- …` 이 붙는 것이 통상이다.**
    그 주석을 안 떼고 `ALTER TABLE` 을 만들면 쉼표가 문장 한복판에 남아
    `near ",": syntax error` 로 죽는다 — 그리고 **새 컬럼에만 일어나므로 평소에는
    초록이고, 컬럼을 더한 날 두 시간 반짜리 수집이 죽는다.** 실제로 그렇게 깨졌다.
    """
    conn = _빈DB()
    선언 = [l for l in 스키마.SCHEMA.splitlines()
           if re.match(r"^  위임수집판본\s+TEXT", l)]
    assert 선언 and "--" in 선언[0], (
        "이 검사는 `법령.위임수집판본` 선언 줄 끝의 주석을 전제로 한다 — 주석이"
        " 사라지면 아무것도 안 잰다. 줄 끝 주석이 달린 다른 NULL 허용 컬럼으로 앵커를 옮겨라")
    conn.execute("ALTER TABLE 법령 DROP COLUMN 위임수집판본")
    assert 이행.컬럼보강(conn) == ["법령.위임수집판본"]


def test_컬럼보강은_두_번_돌려도_같다() -> None:
    conn = _빈DB()
    assert 이행.컬럼보강(conn) == []
    assert 이행.컬럼보강(conn) == []


def test_컬럼보강_뒤에_migrate_가_주석을_갈아_끼운다() -> None:
    """⚠️ **이 둘이 같이 돌아야 한다.** `ALTER` 로 붙인 컬럼에는 주석이 없어서, DB 안의
    DDL 이 주석 없는 상태로 남는다 — 이 DB 는 **주석이 곧 문서**라 그건 문서를 잃는 것이다.

    ⚠️ **컬럼을 떼서 흉내 내지 않고 옛 `SCHEMA` 로 DB 를 만든다.** `ALTER ADD COLUMN` 은
    언제나 맨 뒤에 붙으므로, 가운데 컬럼을 떼고 보강하면 컬럼 순서가 `SCHEMA` 와 갈려
    `migrate()` 가 구조불일치로 물러난다 — 그러면 검사가 이름과 다른 것을 잰다. 여기서
    재는 것은 **실제 업그레이드 경로**(옛 DB → 새 컬럼 → 주석)다.

    앵커가 `법령.위임수집판본` 인 이유는 그것이 **표의 마지막 컬럼**이면서 줄 끝 주석을
    갖기 때문이다. 가운데 컬럼을 고르면 위 이유로 이 검사가 성립하지 않는다.
    """
    import re

    줄 = 스키마.SCHEMA.split(chr(10))
    남길 = []
    for 하나 in 줄:
        if 하나.startswith("  위임수집판본 TEXT"):
            # 주석은 컬럼 줄 끝에 인라인이다(한 주장 한 줄). 뗀 컬럼이 마지막 컬럼이면
            # 앞 컬럼의 쉼표는 CHECK 절이 뒤에 있어 그대로 둬도 DDL 이 읽힌다.
            continue
        남길.append(하나)
    옛 = chr(10).join(남길)
    assert "위임수집판본" not in 옛 and 옛 != 스키마.SCHEMA
    conn = 연결.connect(":memory:")
    conn.executescript(옛)

    assert "법령.위임수집판본" in 이행.컬럼보강(conn)
    # ⚠️ **표식을 손으로 적지 않고 `SCHEMA` 에서 뽑는다.** 문구를 손댄 날 리터럴 앵커는
    #    `not in` 과 `in` 을 **둘 다 통과시켜** 조용히 무의미해진다(실제로 그랬다).
    표식 = next(l for l in 스키마.SCHEMA.splitlines()
              if l.startswith("  위임수집판본 TEXT")).split("--", 1)[1].strip()
    assert len(표식) > 20, 표식
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='법령'").fetchone()[0]
    assert 표식 not in sql, "ALTER 만으로 주석이 붙을 리 없다"
    assert "법령" in 이행.migrate(conn, 백업확인=False)["주석교체"]
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name='법령'").fetchone()[0]
    assert 표식 in sql


def test_재파싱이_저장된_전문에서_주문을_다시_만든다() -> None:
    """⚠️ 이게 없으면 `주문추출` 을 고칠 때마다 헌재 전량을 다시 받아야 한다.
    참조 파싱을 별도 단계로 뗀 것과 같은 이유다."""

    conn = _빈DB()
    conn.execute(
        "INSERT INTO 헌재결정례 (헌재결정례ID, 사건번호, 전문, 수집일시)"
        " VALUES ('1','2009헌바17','【주 문】 형법 제241조는 헌법에 위반된다.【이 유】 1. 개요','x')")
    assert conn.execute("SELECT 주문 FROM 헌재결정례").fetchone()[0] is None
    assert 심판례.주문재추출(conn) == 1
    assert conn.execute("SELECT 주문 FROM 헌재결정례").fetchone()[0] \
        == "형법 제241조는 헌법에 위반된다."
