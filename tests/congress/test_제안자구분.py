"""`제안자구분` 의 `CHECK` 과 그 밖 값을 만났을 때의 처신.

**`표결결과` 와 처신이 다르다.** 저쪽은 CHECK 밖 값이면 그 행을 통째로 빼는데,
의안에 같은 방식을 쓰면 **의안번호에 구멍이 생겨 `A1` 완결성 게이트가 깨진다** —
"원천에 없는 번호"와 "우리가 거부한 번호"를 영원히 구별할 수 없게 된다.
그래서 여기서는 **의안은 넣고 이 칸만 비운다.**
"""

import sqlite3

import pytest

from congress import bills
from congress import db


class Test정규화:
    @pytest.mark.parametrize("값", ["의원", "위원장", "정부", "의장", "기타"])
    def test_아는_값은_그대로_둔다(self, 값):
        assert bills.제안자구분(값) == 값

    def test_모르는_값은_None_이다(self):
        assert bills.제안자구분("새라벨") is None

    def test_없으면_None_이다(self):
        assert bills.제안자구분(None) is None

    def test_CHECK_과_같은_목록에서_나온다(self):
        """⚠️ **파이썬과 SQL 이 반드시 한 목록에서 나와야 한다.** 두 곳에 적으면
        한쪽만 고치는 날이 오고, 그날 수집이 `IntegrityError` 로 죽는다.

        ⚠️ **"각 값이 SCHEMA 어딘가에 있다"로 검사하면 안 된다** — 뷰 정의에도
        `'의원'` 이 있어서 `CHECK` 에서 그 값을 빼도 통과하고, SQL 쪽에만 값이
        하나 더 생기는 경우도 못 잡는다. **집합을 정확히 맞춰야 한다.**
        """
        import re

        m = re.search(r"CHECK \(제안자구분 IN \(([^)]*)\)\)", db.SCHEMA)
        assert m, "SCHEMA 에서 제안자구분 CHECK 절을 못 찾았다"
        sql쪽 = tuple(re.findall(r"'([^']*)'", m.group(1)))
        assert sql쪽 == bills.제안자구분값


class Test두_원천_모두_거른다:
    def test_TVBPMBILL11(self):
        행 = bills.의안행({"BILL_NO": "1", "BILL_ID": "PRC_1", "BILL_NAME": "x",
                          "PROPOSER_KIND": "새라벨"})
        assert 행["제안자구분"] is None

    def test_ALLBILL(self):
        행 = bills.allbill_의안행({"BILL_NO": "1", "BILL_ID": "PRC_1", "BILL_NM": "x",
                                  "PPSR_KND": "새라벨"})
        assert 행["제안자구분"] is None


class TestCHECK:
    def test_CHECK_밖_값을_막는다(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                         " VALUES ('2200001','PRC_1','x','새라벨')")

    def test_아는_값과_NULL_은_통과한다(self, conn):
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                     " VALUES ('2200001','PRC_1','x','기타')")
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                     " VALUES ('2200002','PRC_2','y',NULL)")
        assert conn.execute("SELECT COUNT(*) FROM 의안").fetchone()[0] == 2


class Test막힘으로_남긴다:
    """⚠️ **NULL 로 조용히 넘어가면 안 된다.** 새 라벨이 생긴 날 그 의안들의
    제안 주체가 통째로 비는데, 원장에 아무것도 안 남으면 아무도 모른다.

    ⚠️ `clear_failure` **뒤에** 남겨야 한다 — 앞에 남기면 같은 트랜잭션이 방금 지운다.
    """

    def test_CHECK_밖_값을_원장에_남긴다(self, conn, monkeypatch):
        from congress import net

        행 = {"BILL_NO": "2200001", "BILL_ID": "PRC_1", "BILL_NAME": "x",
              "PROPOSER_KIND": "새라벨", "PROPOSE_DT": "2024-06-01"}

        class 가짜:
            def all_pages(self, *a, **k):
                return iter([행])

        bills.collect_목록(conn, 가짜(), 로그=lambda _: None)

        r = conn.execute(
            "SELECT 실패종류, 상세 FROM 수집실패 WHERE 대상종류='의안상세' AND 대상키='2200001'"
        ).fetchone()
        assert r is not None, "CHECK 밖 값을 만나고도 원장에 아무것도 안 남았다"
        assert r[0] == "막힘" and "새라벨" in r[1]
        assert conn.execute(
            "SELECT 제안자구분 FROM 의안 WHERE 의안번호='2200001'").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM 의안").fetchone()[0] == 1, "의안은 들어와야 한다"


class TestALLBILL_도_원장에_남긴다:
    """⚠️ **`clear_failure` 가 같은 트랜잭션 뒤에 있으면 방금 적은 것이 지워진다.**

    그리고 다음 실행에서는 의안이 이미 있어(`있음`) 제안자구분이 갱신 범위 밖이라
    **다시 기록될 기회도 없다** — 그 의안은 영영 원장에 안 남는다.

    비법률안은 `ALLBILL` 로만 들어오므로 이 경로가 죽으면 그 부류 전체가 사각지대다.
    """

    def test_새_의안의_CHECK_밖_값이_원장에_남는다(self, conn):
        나쁜 = {"BILL_NO": "2200002", "BILL_ID": "PRC_2", "BILL_NM": "결산 승인안",
               "BILL_KND": "승인안", "PPSR_KND": "새라벨"}

        class 가짜:
            """`차집합` 이 범위 안의 빈 번호(2200002)를 대상으로 올리게 만든다."""

            def json(self, api, **kw):
                return ([나쁜] if kw.get("BILL_NO") == "2200002" else [], None)

        bills.collect_보완(conn, 가짜(), {"2200001", "2200003"}, 로그=lambda *a: None)

        assert conn.execute(
            "SELECT 제안자구분 FROM 의안 WHERE 의안번호='2200002'").fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM 의안 WHERE 의안번호='2200002'"
                            ).fetchone()[0] == 1, "의안 자체는 들어와야 한다"
        r = conn.execute(
            "SELECT 실패종류, 상세 FROM 수집실패 WHERE 대상종류='의안상세' AND 대상키='2200002'"
        ).fetchone()
        assert r is not None, "ALLBILL 경로가 CHECK 밖 값을 원장에 안 남겼다"
        assert r[0] == "막힘" and "새라벨" in r[1]
