"""`db.migrate` — 기존 DB 의 스키마 텍스트를 제자리에서 갈아 끼운다.

**이 DB 는 주석이 곧 문서다**(SQLite 가 `CREATE TABLE` 괄호 안의 `--` 를 `sqlite_master.sql`
에 텍스트로 보존한다). 그래서 "주석만 고치는 변경"이 흔한데 — 새 함정을 발견할 때마다 하는
일이다 — `CREATE TABLE IF NOT EXISTS` 는 기존 DB 에 **아무것도 안 한다.**
"""

import pytest

from congress import db


# ⚠️ **앵커는 테이블 이름이다.** 특정 주석 문구를 박으면 그 주석을 손댄 날
#    `.replace()` 가 아무것도 안 바꾸고 **테스트가 조용히 무의미해진다.**
#    (2026-08-22 스키마 재작성이 실제로 옛 앵커를 지웠다 — 그때는 요란하게 깨졌지만,
#     `assert 옛 in schema` 가 없었으면 조용히 통과했을 것이다.)
_위원회 = "CREATE TABLE IF NOT EXISTS 위원회 (\n"


def 주석만_바꾼다(schema: str) -> str:
    assert _위원회 in schema
    return schema.replace(_위원회, _위원회 + "    -- 2026-08-13 주석 갱신\n", 1)


def 컬럼을_더한다(schema: str) -> str:
    assert _위원회 in schema
    return schema.replace(_위원회, _위원회 + "    위원회코드  TEXT,\n", 1)


class Test전제:
    def test_init_schema_만으로는_주석이_반영되지_않는다(self, conn):
        """`migrate` 가 왜 있어야 하는지의 근거. 실측 반영 0건."""
        conn.executescript(주석만_바꾼다(db.SCHEMA))
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='위원회'"
        ).fetchone()[0]
        assert "2026-08-13 주석 갱신" not in 저장된


class Test주석교체:
    def test_주석이_바뀌고_데이터가_보존된다(self, conn):
        db.upsert_위원회(conn, "법제사법위원회")
        결과 = db.migrate(conn, 주석만_바꾼다(db.SCHEMA), 백업확인=False)

        assert "위원회" in 결과["주석교체"]
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='위원회'"
        ).fetchone()[0]
        assert "2026-08-13 주석 갱신" in 저장된
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1

    def test_교체_뒤_무결성과_FK_가_성하다(self, conn):
        """S17. `writable_schema` 는 검증 없이 카탈로그를 갈아 끼운다 — 확인이 필요하다."""
        db.upsert_위원회(conn, "보건복지위원회 법안심사제1소위원회", 상위="보건복지위원회")
        db.migrate(conn, 주석만_바꾼다(db.SCHEMA), 백업확인=False)

        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 2

    def test_바뀐_것이_없으면_아무것도_안_한다(self, conn):
        결과 = db.migrate(conn, db.SCHEMA)
        assert 결과["주석교체"] == []
        assert 결과["구조불일치"] == []


class Test구조가_다르면:
    def test_손대지_않고_이름만_돌려준다(self, conn):
        """S16. `writable_schema` 는 검증을 안 한다.

        구조가 다른 DDL 을 넣으면 **테이블과 카탈로그가 어긋난 채로 열리는데**, 그 상태는
        조용하다가 나중에 이상하게 터진다. 어느 컬럼이 어디로 가는지는 사람이 정한다.
        """
        db.upsert_위원회(conn, "법제사법위원회")
        결과 = db.migrate(conn, 컬럼을_더한다(db.SCHEMA))

        assert 결과["구조불일치"] == ["위원회"]
        assert 결과["주석교체"] == []
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='위원회'"
        ).fetchone()[0]
        assert "위원회코드" not in 저장된
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    def test_구조가_같은_다른_테이블은_그래도_갱신한다(self, conn):
        """한 테이블이 막혔다고 나머지까지 멈추면 주석 동기화가 영원히 안 된다."""
        schema = 컬럼을_더한다(주석만_바꾼다(db.SCHEMA))
        의안 = "CREATE TABLE IF NOT EXISTS 의안 (\n"
        assert 의안 in schema
        결과 = db.migrate(conn, schema.replace(의안, 의안 + "    -- 갱신됨\n", 1),
                          백업확인=False)

        assert 결과["구조불일치"] == ["위원회"]
        assert "의안" in 결과["주석교체"]
