"""위원장 대안의 함정을 산문 대신 뷰가 없앤다.

⚠️ **위원장 대안은 심사 당일 새 번호를 받아 `회의의안` 에 자기 번호로는 없다.** 그래서
   대안 번호로 회의를 물으면 **예외 없이 0건**이 오고, 0건은 "논의가 없었다"로 읽힌다.
   공포된 법률안의 절반 안팎이 이 부류라, 그 오답은 "법이 된 것의 절반"을 통째로 지운다.

SKILL.md 가 이 순서를 세 곳에서 되풀이해 경고하던 자리다. 경고는 읽는 사람이 기억해야
하고, 뷰는 기억하지 않아도 된다.
"""

from __future__ import annotations

import pytest

import db


@pytest.fixture
def 대안(conn):
    """원안 둘이 위원장 대안 하나에 흡수되고, 그 원안이 소위·상임위 회의에 올랐다."""
    db.upsert_위원회(conn, "정무위원회")
    for 번호, 종류, 구분, 대안번호 in [
        ("2200001", "법률안", "의원", "2209999"),
        ("2200002", "법률안", "의원", "2209999"),
        ("2209999", "법률안", "위원장", None),
        ("2200003", "결의안", "의원", None),
    ]:
        conn.execute(
            "INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,"
            "소관위원회,대안의안번호,수집시각) VALUES (?,?,?,?,?,'정무위원회',?,"
            "'2026-08-29 00:00:00')",
            (번호, f"ID{번호}", 종류, f"의안 {번호}", 구분, 대안번호))
    for 회의id, 종류, 위원회, 일자 in [
        (1, "상임위원회", "정무위원회", "2026-06-01"),
        (2, "국정감사", "정무위원회", "2026-10-01"),
    ]:
        conn.execute(
            "INSERT INTO 회의 (회의id,회의종류,위원회명,회의일자) VALUES (?,?,?,?)",
            (회의id, 종류, 위원회, 일자))
    for 회의id, 번호 in [(1, "2200001"), (1, "2200002"), (1, "2200003")]:
        conn.execute(
            "INSERT INTO 회의의안 (회의id,의안번호) VALUES (?,?)", (회의id, 번호))
    return conn


class Test의안회의:
    def test_대안_번호로_물으면_원안이_거친_회의가_온다(self, 대안):
        """**이 뷰가 있는 이유가 이 한 줄이다.** `회의의안` 을 직접 걸면 0건이다."""
        assert 대안.execute(
            "SELECT COUNT(*) FROM 회의의안 WHERE 의안번호='2209999'").fetchone()[0] == 0
        행들 = 대안.execute(
            "SELECT * FROM 의안회의 WHERE 의안번호='2209999' ORDER BY 경유의안번호"
        ).fetchall()
        assert [r["경유의안번호"] for r in 행들] == ["2200001", "2200002"]
        assert {r["경로"] for r in 행들} == {"원안경유"}
        assert {r["회의id"] for r in 행들} == {1}

    def test_원안_여럿이_같은_회의면_회의는_DISTINCT_로_센다(self, 대안):
        """⚠ 행이 여럿인 것이 정상이다 — 경유한 원안이 다르기 때문이다."""
        assert 대안.execute(
            "SELECT COUNT(DISTINCT 회의id) FROM 의안회의 WHERE 의안번호='2209999'"
        ).fetchone()[0] == 1

    def test_보통_의안은_자기_번호로_직접_온다(self, 대안):
        행 = 대안.execute(
            "SELECT * FROM 의안회의 WHERE 의안번호='2200001'").fetchone()
        assert (행["경로"], 행["경유의안번호"]) == ("직접", "2200001")

    def test_회의의_속성이_함께_온다(self, 대안):
        """의안 → 회의 → 발언 으로 가려면 회의종류·위원회명·회의일자가 여기 있어야 한다."""
        행 = 대안.execute(
            "SELECT * FROM 의안회의 WHERE 의안번호='2200001'").fetchone()
        assert (행["회의종류"], 행["위원회명"], 행["회의일자"]) == (
            "상임위원회", "정무위원회", "2026-06-01")

    def test_국정감사는_여기_없다(self, 대안):
        """⚠ 의안을 처리하는 자리가 아니다 — `회의의안` 과 같은 경계다."""
        assert 대안.execute(
            "SELECT COUNT(*) FROM 의안회의 WHERE 회의종류='국정감사'").fetchone()[0] == 0

    def test_비법률안도_자기_번호로_온다(self, 대안):
        """`의안심사` 가 법률안 전용이라 비법률안은 이쪽이 유일한 경로다."""
        assert 대안.execute(
            "SELECT COUNT(*) FROM 의안회의 WHERE 의안번호='2200003'").fetchone()[0] == 1

    def test_의안번호_필터가_인덱스를_탄다(self, 대안):
        """⚠️ `발언` 이 백만 행대라 이 뷰가 전량 스캔이면 그 뒤 조인이 전부 느려진다."""
        계획 = "\n".join(r[3] for r in 대안.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM 의안회의 WHERE 의안번호='2209999'"))
        assert "SCAN 회의의안" not in 계획, f"회의의안을 전량 스캔한다:\n{계획}"
        assert "SCAN 의안" not in 계획, f"의안을 전량 스캔한다:\n{계획}"


def test_옛_정의가_남아_있어도_갈아_끼운다(conn):
    """⚠️ `CREATE VIEW IF NOT EXISTS` 였다면 기존 DB 안에서 옛 정의가 살아남는다."""
    conn.execute("DROP VIEW 의안회의")
    conn.execute("CREATE VIEW 의안회의 AS SELECT 1 AS 가짜")
    db.init_schema(conn)
    컬럼 = [r[1] for r in conn.execute('PRAGMA table_info("의안회의")')]
    assert 컬럼 != ["가짜"] and "경유의안번호" in 컬럼
