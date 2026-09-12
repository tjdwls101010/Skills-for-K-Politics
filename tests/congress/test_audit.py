"""`audit.py` 의 게이트 — 특히 **상수와 DB 가 갈렸는가**."""

import sqlite3

import pytest

from congress import audit
from congress import db


class Test드리프트:
    """**주석이 곧 문서인 DB 에서, 상수만 고치고 `migrate()` 를 안 돌리면 아무 신호가 없다.**

    끊어진 고리가 여기다 — 상수→DB 갱신은 수집 스크립트가 실행 중 `migrate()` 를 부르는 것이
    전부인데, 원천 진단이 빨가면 수집을 건너뛰어 `migrate()` 도 안 돈다. `.gitignore` 가 DB 를
    빼므로 CI 는 접근조차 못 한다. 그러면 **주석 3만 자를 고쳐 놓고 `.schema` 는 옛 주석을
    그대로 내놓는데 그것을 말해 주는 것이 하나도 없다.**
    """

    def test_동기화된_DB_는_통과한다(self, conn):
        assert conn.execute(audit.드리프트질의).fetchone()[0] == 0

    def test_주석_한_글자만_달라도_잡는다(self, conn):
        옛 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='의안'"
        ).fetchone()[0]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='의안'",
            (옛 + "  -- 몰래 붙은 주석",),
        )
        conn.execute("PRAGMA writable_schema=OFF")
        assert conn.execute(audit.드리프트질의).fetchone()[0] == 1

    def test_빈_DB_에서_빨갛다(self, db_path):
        """⚠️ **테이블이 하나도 없는 DB 를 통과시키면 게이트가 아니다.**

        "어긋난 행이 없는가"로 물으면 모집단이 0 이라 조용히 초록이 된다. 그래서
        **기대 목록에서 출발해 DB 쪽을 LEFT JOIN** 한다 — 없는 테이블이 곧 위반이다.
        """
        빈 = sqlite3.connect(db_path)
        try:
            assert 빈.execute(audit.드리프트질의).fetchone()[0] == len(
                list(db._테이블문.finditer(db.SCHEMA))
            )
        finally:
            빈.close()

    def test_게이트_목록에_실제로_올라_있다(self):
        """검사를 써 놓고 `게이트` 에 안 넣으면 아무 데서도 안 돈다."""
        assert any(sql == audit.드리프트질의 for _, _, sql in audit.게이트)


class Test스키마출력:
    """`schema` 명령이 **드리프트 탐지기**이기도 하다.

    상수를 내면 "내가 본 스키마"와 "클로드가 `.schema` 로 보는 것"이 조용히 갈린다.
    """

    def test_DB_가_있으면_DB_것을_낸다(self, conn, db_path):
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = sql || '  -- DB 에만 있는 표시'"
            " WHERE type='table' AND name='의안'"
        )
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
        본문, 경고 = db.스키마출력(db_path, ("의안",))
        assert "DB 에만 있는 표시" in 본문
        assert 경고 is not None and "낡았다" in 경고

    def test_동기화돼_있으면_경고가_없다(self, conn, db_path):
        본문, 경고 = db.스키마출력(db_path, ("의안",))
        assert 경고 is None
        assert "CREATE TABLE 의안" in 본문

    def test_DB_가_없으면_상수를_내며_그렇다고_밝힌다(self, tmp_path):
        본문, 경고 = db.스키마출력(tmp_path / "없다.db")
        assert 본문 == db.SCHEMA.strip()
        assert 경고 is not None and "상수" in 경고

    def test_경고는_본문에_섞이지_않는다(self, conn, db_path):
        """⚠️ 이 출력은 그대로 읽히는 스키마 본문이다. 경고가 stdout 에 섞이면
        그 줄이 스키마의 일부로 읽힌다 — 그래서 둘을 따로 돌려준다."""
        본문, 경고 = db.스키마출력(db_path, ("의안",))
        assert "⚠️" not in (본문.splitlines()[0] if 본문 else "")
        assert 경고 is None or 경고 not in 본문


@pytest.fixture
def 채운(conn):
    """뷰 둘이 **비지 않도록** 최소한을 넣는다 — 빈 것도 위반이기 때문이다."""
    db.upsert_위원회(conn, "정무위원회")
    conn.execute(
        "INSERT INTO 의원 (의원코드,이름,정당) VALUES ('M1','아무개','조국혁신당')")
    for 번호, 구분, 대안 in [("2200001", "의원", "2209999"), ("2209999", "위원장", None)]:
        conn.execute(
            "INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,소관위원회,"
            "대안의안번호,수집시각) VALUES (?,?,'법률안',?,?,'정무위원회',?,"
            "'2026-08-29 00:00:00')", (번호, f"ID{번호}", f"의안 {번호}", 구분, 대안))
    conn.execute(
        "INSERT INTO 발의자 (의안번호,의원코드,역할) VALUES ('2200001','M1','대표발의')")
    conn.execute(
        "INSERT INTO 회의 (회의id,회의종류,위원회명,회의일자)"
        " VALUES (1,'상임위원회','정무위원회','2026-06-01')")
    conn.execute("INSERT INTO 회의의안 (회의id,의안번호) VALUES (1,'2200001')")
    return conn


class Test뷰게이트:
    """⚠️ **깨진 뷰는 조회할 때야 터진다.** `PRAGMA integrity_check` 도 통과하고
    `sqlite_master` 에도 멀쩡히 앉아 있어서, 실제로 조회해 보는 것 말고 확인할 방법이 없다.

    이 DB 의 뷰는 함정을 대신 지키는 인터페이스라 조용히 죽으면 그 함정이 통째로
    되살아난다 — `의안회의` 가 없으면 위원장 대안의 논의가 예외 없이 0건이 된다.
    """

    def test_뷰가_다_차_있으면_초록이다(self, 채운):
        assert audit._깨진뷰상세(채운)[1] == []

    def test_뷰_하나를_지우면_빨갛다(self, 채운):
        채운.execute("DROP VIEW 의안회의")
        _, 난것 = audit._깨진뷰상세(채운)
        assert len(난것) == 1 and 난것[0].startswith("의안회의")

    def test_뷰가_살았지만_비어도_빨갛다(self, 채운):
        """필터가 전부를 걸러내는 상태가 실재하고, 그러면 0건이 조용히 답이 된다."""
        채운.execute("DROP VIEW 법률안제안주체")
        채운.execute("CREATE VIEW 법률안제안주체 AS SELECT 의안번호 FROM 의안 WHERE 0")
        assert "법률안제안주체(비었다)" in audit._깨진뷰상세(채운)[1]

    def test_목록을_SCHEMA_에서_뽑는다(self):
        """⚠️ 손으로 적으면 뷰를 더한 날 그 뷰만 아무도 안 지킨다."""
        assert set(audit._뷰이름들(db.SCHEMA)) == {"법률안제안주체", "의안회의"}


class Test게이트_A9:
    """가결·부결인데 본회의 심사행이 없는 의안 — **법률안에만** 묻는다.

    `의안심사` 의 원천(`TVBPMBILL11`·`TVBPMCONFINFO`)은 법률안 전용이라 비법률안은 본회의 행이
    구조적으로 없다. 비법률안 처리결과를 `ALLBILL` 로 채우자(#86) 가결 결의안 363건이 이 게이트를
    빨갛게 했다 — 적재가 덜 된 것이 아니라 원천이 안 주는 것이다.
    """

    def _A9(self, conn):
        게, _, _ = audit.run(conn)
        return next(v for 번호, _, v, _ in 게 if 번호 == "A9")

    def _의안(self, conn, 번호, 종류):
        conn.execute(
            "INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,처리결과)"
            " VALUES (?,?,?,?,'정부','원안가결')", (번호, f"ID{번호}", 종류, f"의안 {번호}"))

    def test_가결된_비법률안은_본회의_행이_없어도_위반이_아니다(self, conn):
        self._의안(conn, "2200051", "결산")
        assert self._A9(conn) == 0

    def test_가결된_법률안에_본회의_행이_없으면_위반이다(self, conn):
        self._의안(conn, "2200052", "법률안")
        assert self._A9(conn) == 1
