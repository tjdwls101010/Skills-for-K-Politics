"""`db._테이블재구축` — `ALTER TABLE` 로 못 하는 변경을 통째로 다시 만들어 낸다.

**이 파일이 지키는 것은 하나다: 행을 흘리지 않는다.**

law 는 이 절차를 한 번 겪었지만 congress 는 처음이다(계획 260822 「남은 위험」).
`의안` 은 자식 넷이 `ON DELETE CASCADE` 로 매달린 부모라, 절차를 틀리면
**수십만 행이 조용히 사라지는데 `foreign_key_check` 는 깨끗하다고 답한다.**
"""

import re
import sqlite3

import pytest

from congress import db


_CHECK절 = "\n                        CHECK (제안자구분 IN ('의원','위원장','정부','의장','기타'))"


def _CHECK_없는_SCHEMA() -> str:
    """`CHECK` 이 붙기 **전**의 모양. 재구축이 실제로 밟는 방향이 이쪽 → 현재 SCHEMA 다."""
    옛 = db.SCHEMA.replace(_CHECK절, "", 1)
    assert 옛 != db.SCHEMA, "SCHEMA 에서 CHECK 절을 못 찾았다 — 이 테스트가 무의미해졌다"
    return 옛


@pytest.fixture
def 채운conn(db_path):
    """**CHECK 이 없던 시절의 DB** 를 만들고 부모 하나와 자식 넷에 행을 심는다."""
    conn = db.connect(db_path)
    conn.executescript(_CHECK_없는_SCHEMA())
    db.upsert_위원회(conn, "정무위원회")
    conn.execute(
        "INSERT INTO 의원 (의원코드, 이름, 정당) VALUES ('A1','가','조국혁신당'),('A2','나','조국혁신당')"
    )
    for n in ("2200001", "2200002"):
        db.upsert_의안(conn, "TVBPMBILL11", {
            "의안번호": n, "의안ID": f"PRC_{n}", "의안명": f"법률안{n}",
            "의안종류": "법률안", "제안자구분": "의원", "소관위원회": "정무위원회",
        })
    conn.executemany("INSERT INTO 의안심사 (의안번호, 단계, 회부일) VALUES (?,?,?)",
                     [("2200001", "소관위", "2024-06-01"), ("2200002", "본회의", "2024-07-01")])
    conn.executemany("INSERT INTO 발의자 (의안번호, 의원코드, 역할) VALUES (?,?,?)",
                     [("2200001", "A1", "대표발의"), ("2200001", "A2", "공동발의")])
    conn.execute("INSERT INTO 표결집계 (의안번호, 의결일, 찬성수) VALUES ('2200002','2024-07-01',200)")
    conn.execute("INSERT INTO 표결 (의안번호, 의원코드, 표결결과) VALUES ('2200002','A1','찬성')")
    conn.commit()
    yield conn
    conn.close()


def _센다(conn):
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ("의안", "의안심사", "발의자", "표결집계", "표결")}


class Test재구축:
    def test_행을_흘리지_않는다(self, 채운conn):
        전 = _센다(채운conn)
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        assert _센다(채운conn) == 전

    def test_자식이_CASCADE_로_사라지지_않는다(self, 채운conn):
        """⚠️ **가장 나쁜 실패 모드다.** `foreign_keys=ON` 인 채로 부모를 RENAME 하면
        SQLite 3.25+ 가 자식의 `REFERENCES` 를 새 이름으로 고쳐 쓰고, 그 다음
        `DROP TABLE` 이 자식을 CASCADE 로 데려간다. **그 상태에서
        `foreign_key_check` 는 깨끗하다고 답한다** — 없는 테이블에는 걸 제약이 없어서다.
        """
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        for 자식 in ("의안심사", "발의자", "표결집계", "표결"):
            ddl = 채운conn.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (자식,)).fetchone()[0]
            assert "_옛_" not in ddl, f"{자식} 이 사라진 테이블을 가리킨다"
        assert 채운conn.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_인덱스가_되살아난다(self, 채운conn):
        """⚠️ `DROP TABLE` 이 그 테이블의 인덱스를 함께 지운다. 안 되살리면
        조회가 조용히 풀스캔이 된다 — 에러가 없으므로 아무도 모른다."""
        전 = sorted(r[0] for r in 채운conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        후 = sorted(r[0] for r in 채운conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"))
        assert 전 == 후

    def test_두_번_돌려도_같다(self, 채운conn):
        """⚠️ **판정은 컬럼 목록이 아니라 구조다.** 컬럼이 그대로인 채 제약만 붙는
        변경이라, 컬럼 차집합으로 물으면 **첫 번도 조용히 건너뛴다.**"""
        assert db._테이블재구축(채운conn, lambda _: None, ("의안",)) == 1
        assert db._테이블재구축(채운conn, lambda _: None, ("의안",)) == 0

    def test_CHECK_이_재구축_뒤_실제로_막는다(self, 채운conn):
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        with pytest.raises(sqlite3.IntegrityError):
            채운conn.execute(
                "INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                " VALUES ('2299999','PRC_X','x','새라벨')")

    def test_재구축_뒤에도_migrate_가_주석을_갈아_끼운다(self, 채운conn):
        """⚠️ `ALTER TABLE RENAME` 이 남기는 `CREATE TABLE "이름"` 형태로 만들면
        따옴표 한 쌍 때문에 `migrate()` 가 영원히 구조불일치로 물러난다 —
        **그러면 이 DB 의 주석(=문서)이 안에서 낡은 채로 남는다.**"""
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        앵커 = "CREATE TABLE IF NOT EXISTS 의안 (\n"
        결과 = db.migrate(채운conn, db.SCHEMA.replace(앵커, 앵커 + "    -- 갱신됨\n", 1),
                          백업확인=False)
        assert 결과["구조불일치"] == [] and "의안" in 결과["주석교체"]

    def test_행이_새면_예외를_던지고_되돌린다(self, 채운conn):
        """행 수 대조가 그물이다. 옮기다 흘리면 트랜잭션이 통째로 롤백돼야 한다."""

        class 새는연결:
            """`INSERT … SELECT` 에만 조건을 붙여 한 행을 흘린다."""

            def __init__(self, c):
                self._c = c

            def execute(self, sql, *a):
                if sql.startswith("INSERT INTO 의안 ("):
                    sql += " WHERE 의안번호 <> '2200001'"
                return self._c.execute(sql, *a)

            def __getattr__(self, n):
                return getattr(self._c, n)

        with pytest.raises(RuntimeError, match="행이 샜다"):
            db._테이블재구축(새는연결(채운conn), lambda _: None, ("의안",))

        assert 채운conn.execute("SELECT COUNT(*) FROM 의안").fetchone()[0] == 2
        assert 채운conn.execute("SELECT COUNT(*) FROM 발의자").fetchone()[0] == 2
        assert 채운conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='_옛_의안'").fetchone()[0] == 0


class Test컬럼삭제:
    def test_컬럼이_사라진다(self, 채운conn):
        db._컬럼삭제(채운conn, lambda _: None, (("의안", "공포법률명"),))
        assert "공포법률명" not in [r[1] for r in 채운conn.execute("PRAGMA table_info(의안)")]

    def test_이미_없으면_멱등이다(self, 채운conn):
        db._컬럼삭제(채운conn, lambda _: None, (("의안", "공포법률명"),))
        assert db._컬럼삭제(채운conn, lambda _: None, (("의안", "공포법률명"),)) == 0


class Test부분_실패가_인덱스를_잃지_않는다:
    """⚠️ **커밋과 인덱스 복원이 갈라져 있으면 반쪽 상태가 남는다.**

    테이블은 자기 트랜잭션에서 커밋되는데 인덱스는 루프가 **다 끝난 뒤** 한 번에
    되살아난다. 그 사이에서 무엇이든 터지면 — 로그가 예외를 내든, 다음 테이블이
    실패하든 — 앞 테이블은 이미 확정됐고 인덱스만 사라진다. **그리고 다시 돌려도
    구조가 이미 같다는 이유로 아무것도 안 한다.** 조회가 조용히 풀스캔이 된다.
    """

    def test_뒤에서_터져도_인덱스가_남아_있다(self, 채운conn):
        전 = sorted(r[0] for r in 채운conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='의안'"
            " AND sql IS NOT NULL"))
        assert 전, "전제: 의안에 인덱스가 있다"

        def 터지는로그(_):
            raise BrokenPipeError("로그가 죽었다")

        with pytest.raises(BrokenPipeError):
            db._테이블재구축(채운conn, 터지는로그, ("의안",))

        후 = sorted(r[0] for r in 채운conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='의안'"
            " AND sql IS NOT NULL"))
        assert 후 == 전, f"인덱스가 사라졌다: {sorted(set(전) - set(후))}"

    def test_SCHEMA_밖_인덱스도_살아남는다(self, 채운conn):
        """⚠️ 운영자가 손으로 만든 인덱스는 `SCHEMA` 에 없다. `executescript(SCHEMA)` 만
        다시 돌리면 그런 인덱스는 **재구축 때마다 조용히 사라진다.**"""
        채운conn.execute("CREATE INDEX idx_손으로_만든것 ON 의안(공포일)")
        db._테이블재구축(채운conn, lambda _: None, ("의안",))
        assert 채운conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='idx_손으로_만든것'"
        ).fetchone()[0] == 1


class Test구조_비교가_리터럴을_건드리지_않는다:
    """⚠️ **`_구조()` 가 문자열 리터럴 안의 공백까지 지우면 `migrate()` 가 거짓말한다.**

    `IN ('a, b')` 와 `IN ('a,b')` 를 같은 구조로 보면, 실제 제약이 바뀐 DDL 을
    "주석만 바뀐 것"으로 오인해 `writable_schema` 로 갈아 끼운다. 그러면 **카탈로그와
    테이블 실체가 어긋난 채로 열린다** — 그 상태는 조용하다.
    """

    def test_리터럴_안의_공백은_유의미하다(self):
        a = "CREATE TABLE t (v TEXT CHECK (v IN ('a, b')))"
        b = "CREATE TABLE t (v TEXT CHECK (v IN ('a,b')))"
        assert db._구조(a) != db._구조(b)

    def test_리터럴_밖의_공백은_무시한다(self):
        a = "CREATE TABLE t (\n  v TEXT CHECK (v IN ('a','b'))\n)"
        b = "CREATE TABLE t (v TEXT CHECK(v IN ('a','b')))"
        assert db._구조(a) == db._구조(b)
