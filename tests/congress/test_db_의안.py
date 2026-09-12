"""`upsert_의안` — 원천별 쓰기 범위.

계획이 "이 설계에서 가장 위험한 자리"라고 부르는 곳이다. `TVBPMBILL11` 이 **매 실행 전량**
UPSERT 되는데, 그 UPSERT 가 자기가 모르는 컬럼까지 `SET` 하면 나머지 세 원천이 채운 값이
**매일 아침 NULL 로 되돌아간다.** 옛 프로젝트가 정확히 이 방식으로 20,598건의 의안종류를
전부 '미상'으로 되돌렸고, 그 사이 게이트는 아무것도 검사하지 않으면서 초록불이었다.
"""

import re
from pathlib import Path

import pytest

from congress import db


법률안 = {
    "의안번호": "2200851",
    "의안ID": "PRC_X2A4X0",
    "의안명": "국회법 일부개정법률안",
    "제안자구분": "의원",
    "제안일": "2024-06-01",
    "소관위원회": "국회운영위원회",
    "처리결과": "수정가결",
}


@pytest.fixture
def 의안conn(conn):
    db.upsert_위원회(conn, "국회운영위원회")
    return conn


class Test원천별_쓰기범위:
    def test_전량_재수집이_다른_원천의_값을_지우지_않는다(self, 의안conn):
        """S2. 네 원천을 순서대로 쓴 뒤 `TVBPMBILL11` 만 다시 쓴다."""
        db.upsert_의안(의안conn, "TVBPMBILL11", 법률안)
        db.upsert_의안(
            의안conn,
            "ALLBILL",
            {
                "의안번호": "2200851",
                "의안종류": "법률안",
                "정부이송일": "2025-01-02",
                "공포일": "2025-04-22",
                "공포법률명": "국회법",
                "공포번호": "20952",
            },
        )
        db.upsert_의안(
            의안conn,
            "BPMBILLSUMMARY",
            {"의안번호": "2200851", "제안이유및주요내용": "제안이유 본문"},
        )
        db.upsert_의안(
            의안conn, "anBillInfo.do", {"의안번호": "2200851", "대안의안번호": "2220259"}
        )

        # 다음 날 아침. TVBPMBILL11 이 전량을 다시 쓴다 — 처리결과만 바뀌었다.
        db.upsert_의안(의안conn, "TVBPMBILL11", {**법률안, "처리결과": "원안가결"})

        row = 의안conn.execute(
            "SELECT * FROM 의안 WHERE 의안번호='2200851'"
        ).fetchone()
        assert row["처리결과"] == "원안가결", "자기 컬럼은 갱신돼야 한다"
        assert row["의안종류"] == "법률안"
        assert row["정부이송일"] == "2025-01-02"
        assert row["공포일"] == "2025-04-22", (
            "공포일이 지워지면 '법이 됐나'가 항상 거짓이 되는데 에러는 안 난다"
        )
        assert row["공포법률명"] == "국회법"
        assert row["공포번호"] == "20952"
        assert row["제안이유및주요내용"] == "제안이유 본문"
        assert row["대안의안번호"] == "2220259"
    def test_ALLBILL_이_소관위원회를_갱신하지_않는다(self, 의안conn):
        """`ALLBILL.JRCMIT_NM` 은 `GOV_` 행에서 '본회의'를 준다(2200851 실측).

        그대로 갱신하면 `위원회` 테이블에 '본회의'가 생기고 소관위원회가 거기로 옮겨간다.
        `GOV_` 행을 거르더라도 이 컬럼의 정본은 `TVBPMBILL11.CURR_COMMITTEE` 다 —
        `ALLBILL` 은 2,005건에만 부르므로 나머지 90%와 규칙이 달라진다.
        """
        db.upsert_의안(의안conn, "TVBPMBILL11", 법률안)
        db.upsert_위원회(의안conn, "본회의")
        db.upsert_의안(
            의안conn,
            "ALLBILL",
            {"의안번호": "2200851", "소관위원회": "본회의", "의안종류": "법률안"},
        )
        assert (
            의안conn.execute(
                "SELECT 소관위원회 FROM 의안 WHERE 의안번호='2200851'"
            ).fetchone()[0]
            == "국회운영위원회"
        )

    def test_ALLBILL_이_비법률안_행을_새로_만든다(self, 의안conn):
        """차집합 525건은 `ALLBILL` 이 **유일한** 원천이다 — 만들 수 있어야 한다.

        갱신 범위가 5컬럼인 것과, 새 행을 만들 때 쓸 수 있는 컬럼이 5개인 것은 다른 얘기다.
        `의안ID`·`의안명` 이 NOT NULL 이라 5컬럼만으로는 INSERT 자체가 불가능하다.
        """
        db.upsert_의안(
            의안conn,
            "ALLBILL",
            {
                "의안번호": "2200002",
                "의안ID": "PRC_ZZZ",
                "의안명": "2024년도 예산안",
                "의안종류": "예산안",
                "제안자구분": "정부",
                "제안일": "2024-09-01",
            },
        )
        row = 의안conn.execute("SELECT * FROM 의안 WHERE 의안번호='2200002'").fetchone()
        assert row["의안명"] == "2024년도 예산안"
        assert row["의안종류"] == "예산안"
        assert row["제안자구분"] == "정부"


class Test거부해야_하는_것:
    def test_모르는_원천은_거부한다(self, 의안conn):
        with pytest.raises(ValueError, match="모르는 원천"):
            db.upsert_의안(의안conn, "BILLRCPV2", {"의안번호": "2200851"})

    def test_원천이_쓸_수_없는_컬럼은_거부한다(self, 의안conn):
        """조용히 무시하면 '썼는데 반영이 안 된' 상태가 되고 그게 가장 찾기 어렵다."""
        db.upsert_의안(의안conn, "TVBPMBILL11", 법률안)
        with pytest.raises(ValueError, match="쓸 수 없는 컬럼"):
            db.upsert_의안(
                의안conn, "TVBPMBILL11", {"의안번호": "2200851", "공포일": "2025-04-22"}
            )

    def test_갱신전용_원천이_없는_의안을_만나면_터진다(self, 의안conn):
        """`UPDATE … WHERE` 가 0행이면 조용히 성공한 것처럼 보인다.

        제안이유는 의안이 이미 있다는 전제 위에서만 돈다. 전제가 깨졌으면 그 자리에서
        알아야지, 매일 아무것도 안 쓰면서 초록불이면 안 된다.
        """
        with pytest.raises(LookupError, match="없는 의안"):
            db.upsert_의안(
                의안conn,
                "BPMBILLSUMMARY",
                {"의안번호": "9999999", "제안이유및주요내용": "x"},
            )


class Test비법률안_처리결과:
    def test_ALLBILL_이_있는_행의_처리결과를_채운다(self, 의안conn):
        """`TVBPMBILL11` 은 법률안만 주므로 비법률안의 본회의 결과는 `ALLBILL` 이 유일하다."""
        db.upsert_의안(의안conn, "ALLBILL", {
            "의안번호": "2200051", "의안ID": "PRC_ZZZ", "의안명": "2023회계연도 결산",
            "의안종류": "결산", "제안자구분": "정부", "제안일": "2024-05-31",
        })
        db.upsert_의안(의안conn, "ALLBILL", {"의안번호": "2200051", "처리결과": "원안가결"})
        assert 의안conn.execute(
            "SELECT 처리결과 FROM 의안 WHERE 의안번호='2200051'").fetchone()[0] == "원안가결"
