"""`bills.py` — 의안 · 의안심사의 순수 파싱."""

import pytest

from congress import bills

# TVBPMBILL11 실제 응답 한 행(2201037, 소관위 원안가결). 필드명은 실측 24개 중 쓰는 것만.
계류행 = {
    "BILL_ID": "PRC_X2V6W0",
    "BILL_NO": "2220544",
    "BILL_NAME": "중대범죄수사청 조직 및 운영에 관한 법률 일부개정법률안",
    "PROPOSER_KIND": "의원",
    "PROPOSE_DT": "2026-08-11",
    "CURR_COMMITTEE": "행정안전위원회",
    "COMMITTEE_DT": "2026-08-11",
    "CMT_PRESENT_DT": None,
    "CMT_PROC_DT": None,
    "COMMITTEE_PROC_DT": None,
    "CMT_PROC_RESULT_CD": None,
    "LAW_SUBMIT_DT": None,
    "LAW_PRESENT_DT": None,
    "LAW_PROC_DT": None,
    "LAW_PROC_RESULT_CD": None,
    "PROC_DT": None,
    "PROC_RESULT_CD": None,
    "RST_MONA_CD": "CCU1009B",
}
처리행 = {
    **계류행,
    "BILL_NO": "2201037",
    "CMT_PRESENT_DT": "2024-08-19",
    "CMT_PROC_DT": "2024-08-21",
    "CMT_PROC_RESULT_CD": "원안가결",
    "LAW_SUBMIT_DT": "2024-08-21",
    "LAW_PRESENT_DT": "2024-08-28",
    "LAW_PROC_DT": "2024-08-28",
    "LAW_PROC_RESULT_CD": "원안가결",
    "PROC_DT": "2024-08-28",
    "PROC_RESULT_CD": "원안가결",
}


class Test의안행:
    def test_의안종류는_상수_법률안이다(self):
        """P1. 이 API 는 법률안만 준다(결의안·예산안·동의안 전부 `INFO-200`).

        `ALLBILL` 에만 맡기면 그 API 를 2,005건에만 부르므로 **계류 법률안 1만 4천 건의
        의안종류가 영구히 NULL** 이 되고, 재수집 조건 `의안종류 IS NULL` 도 함께 무너진다.
        """
        assert bills.의안행(계류행)["의안종류"] == "법률안"

    def test_쓰기_범위_밖_컬럼을_만들지_않는다(self):
        """`db.upsert_의안` 이 거부하므로 여기서 미리 맞춰 둔다."""
        from congress import db

        assert set(bills.의안행(계류행)) - {"의안번호"} <= set(
            db.의안_원천["TVBPMBILL11"].생성
        )

    def test_계류_의안의_처리결과는_NULL_이다(self):
        assert bills.의안행(계류행)["처리결과"] is None
        assert bills.의안행(처리행)["처리결과"] == "원안가결"


class Test심사행:
    def test_계류_의안은_소관위_회부_한_행뿐이다(self):
        행들 = bills.심사행들(계류행)
        assert [r["단계"] for r in 행들] == ["소관위"]
        assert 행들[0]["회부일"] == "2026-08-11"
        assert 행들[0]["처리일"] is None
        assert 행들[0]["위원회명"] == "행정안전위원회"

    def test_처리된_의안은_세_단계가_다_생긴다(self):
        행들 = {r["단계"]: r for r in bills.심사행들(처리행)}
        assert set(행들) == {"소관위", "법사위", "본회의"}
        assert 행들["소관위"] == {
            "단계": "소관위",
            "위원회명": "행정안전위원회",
            "회부일": "2026-08-11",
            "상정일": "2024-08-19",
            "처리일": "2024-08-21",
            "처리결과": "원안가결",
        }

    def test_소관위_처리일은_CMT_PROC_DT_다(self):
        """⚠️ **`COMMITTEE_PROC_DT` 를 쓰면 안 된다.** 이름이 더 자연스럽게 읽히는 쪽이 함정이다.

        실측(페이지20, 1,000행): 소관위 처리결과가 있는 459행 중 **448행에서
        `COMMITTEE_PROC_DT` 가 NULL** 이고 `CMT_PROC_DT` 에만 날짜가 있다.
        잘못 고르면 소관위 처리일의 97%가 NULL 이 되는데 **에러가 안 난다.**
        """
        행 = {**처리행, "COMMITTEE_PROC_DT": None, "CMT_PROC_DT": "2024-08-21"}
        소관위 = next(r for r in bills.심사행들(행) if r["단계"] == "소관위")
        assert 소관위["처리일"] == "2024-08-21"

    def test_법사위_위원회명은_우리가_채운다(self):
        """원천이 법사위 칸을 안 준다 — 언제나 '법제사법위원회' 다."""
        법사위 = next(r for r in bills.심사행들(처리행) if r["단계"] == "법사위")
        assert 법사위["위원회명"] == "법제사법위원회"

    def test_본회의는_위원회명이_없다(self):
        """⚠️ NULL 이 정상이다 — 원천에 위원회 칸이 아예 없다."""
        본회의 = next(r for r in bills.심사행들(처리행) if r["단계"] == "본회의")
        assert 본회의["위원회명"] is None
        assert 본회의["회부일"] is None and 본회의["상정일"] is None

    def test_소관위원회가_없으면_소관위_행도_없다(self):
        """회부 전이면 `CURR_COMMITTEE` 가 NULL 이다(실측 45건 / 20,019)."""
        행 = {**계류행, "CURR_COMMITTEE": None, "COMMITTEE_DT": None}
        assert [r["단계"] for r in bills.심사행들(행)] == []


class TestALLBILL:
    본체 = {
        "BILL_ID": "PRC_H2P4O0", "BILL_NO": "2200851", "BILL_KND": "법률안",
        "BILL_NM": "방송법 일부개정법률안", "PPSR_KND": "의원", "PPSL_DT": "2024-06-24",
        "JRCMIT_NM": "과학기술정보방송통신위원회", "GVRN_TRSF_DT": "2025-04-17",
        "PROM_LAW_NM": "방송법", "PROM_DT": "2025-04-22", "PROM_NO": "20952",
    }
    재의 = {
        "BILL_ID": "GOV_H2P4O0", "BILL_NO": "2200851", "BILL_KND": "법률안",
        "BILL_NM": "방송법 일부개정법률안", "PPSR_KND": "정부", "PPSL_DT": "2025-01-22",
        "JRCMIT_NM": "본회의", "RGS_PRSNT_DT": "2025-04-17",
        "RGS_RSLN_DT": "2025-04-17", "RGS_CONF_RSLT": "원안가결",
    }

    def test_GOV_가_먼저_와도_PRC_를_본체로_집는다(self):
        """⚠️ **`row[0]` 을 집지 마라.** 실측 2200851 은 `GOV_` 가 먼저 온다.

        `GOV_` 를 본체로 저장하면 `의안ID` 가 그걸로 굳고, 그러면 **표결 조회가 `INFO-200` 을
        돌려주며 공포 3종도 전부 NULL 이 된다.**
        """
        본체, 재의 = bills.split_allbill([self.재의, self.본체])
        assert 본체["BILL_ID"].startswith("PRC_")
        assert 재의["BILL_ID"].startswith("GOV_")

    def test_정부제출은_ARC_도_본체다(self):
        arc = {**self.본체, "BILL_ID": "ARC_X1"}
        본체, 재의 = bills.split_allbill([arc])
        assert 본체["BILL_ID"] == "ARC_X1"
        assert 재의 is None

    def test_재의_행이_없으면_None(self):
        _, 재의 = bills.split_allbill([self.본체])
        assert 재의 is None

    def test_본체가_없으면_거부한다(self):
        """`GOV_` 만 오는 것은 우리가 모르는 상태다. 조용히 넘기면 그 의안이 사라진다."""
        with pytest.raises(ValueError, match="본체"):
            bills.split_allbill([self.재의])

    def test_의안행이_공포_3종과_이송일을_담는다(self):
        값 = bills.allbill_의안행(self.본체)
        assert 값["공포일"] == "2025-04-22"
        assert 값["공포법률명"] == "방송법"
        assert 값["공포번호"] == "20952"
        assert 값["정부이송일"] == "2025-04-17"
        assert 값["의안종류"] == "법률안"

    def test_재의행이_의안심사_모양이_된다(self):
        """`GOV_` 행의 존재 자체가 '정부가 재의를 요구했다'는 뜻이다."""
        assert bills.재의행(self.재의) == {
            "단계": "재의",
            "위원회명": None,
            "회부일": None,
            "상정일": "2025-04-17",
            "처리일": "2025-04-17",
            "처리결과": "원안가결",
        }

    def test_ALLBILL_은_소관위원회를_갱신용으로_내지_않는다(self):
        """⚠️ `GOV_` 행의 `JRCMIT_NM` 은 '본회의' 다. 그대로 넣으면 위원회 테이블이 오염된다.

        `db.upsert_의안` 이 갱신 범위로 막지만, 여기서도 본체 행의 값만 낸다.
        """
        from congress import db

        assert set(bills.allbill_의안행(self.본체)) - {"의안번호"} <= set(
            db.의안_원천["ALLBILL"].생성
        )


class Test위원회_해소:
    def test_표기가_흔들린_위원회명을_저장된_이름으로_바꿔치기한다(self, conn):
        """실측 회귀: 22대 의안 632번째(2219908)에서 이것 때문에 FK 가 터졌다.

        회의록 목록 API 는 `'기후위기특별위원회'`, `TVBPMBILL11.CURR_COMMITTEE` 는
        `'기후위기 특별위원회'`(공백)로 같은 위원회를 다르게 적는다. `upsert_위원회` 가
        저장된 이름을 돌려주는데 **그 반환값을 안 쓰면 아무 소용이 없다.**
        """
        from congress import db

        db.upsert_위원회(conn, "기후위기특별위원회")  # 회의록 API 표기가 먼저 들어와 있다
        값 = {"소관위원회": "기후위기 특별위원회"}
        행들 = [{"단계": "소관위", "위원회명": "기후위기 특별위원회"}]

        bills.위원회_해소(conn, 값, 행들)

        assert 값["소관위원회"] == "기후위기특별위원회"
        assert 행들[0]["위원회명"] == "기후위기특별위원회"
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1

    def test_해소를_거치면_FK_가_통과한다(self, conn):
        """해소 없이 넣으면 `IntegrityError` 다 — 그게 실제로 일어난 일이다."""
        import sqlite3

        from congress import db

        db.upsert_위원회(conn, "기후위기특별위원회")
        db.upsert_의안(
            conn,
            "TVBPMBILL11",
            {"의안번호": "1", "의안ID": "P1", "의안명": "가", "의안종류": "법률안"},
        )
        with pytest.raises(sqlite3.IntegrityError):
            db.replace_의안심사(
                conn, "1", ("소관위",), [{"단계": "소관위", "위원회명": "기후위기 특별위원회"}]
            )

        행들 = [{"단계": "소관위", "위원회명": "기후위기 특별위원회"}]
        bills.위원회_해소(conn, {}, 행들)
        db.replace_의안심사(conn, "1", ("소관위",), 행들)
        assert conn.execute("SELECT COUNT(*) FROM 의안심사").fetchone()[0] == 1


class Test소위:
    def test_소위명이_있으면_상위와_이어_붙인다(self):
        행 = bills.소위행(
            {
                "COMMITTEE_NAME": "보건복지위원회",
                "SUB_COMMITTEE_NAME": "법안심사제1소위원회",
                "SUBMIT_DT": "2025-11-12",
                "PRESENT_DT": "2025-11-18",
                "PROC_DT": "2026-01-07",
                "PROC_RESULT_CD": "대안반영폐기",
            }
        )
        assert 행["위원회명"] == "보건복지위원회 법안심사제1소위원회"
        assert 행["단계"] == "소위"
        assert 행["회부일"] == "2025-11-12"

    def test_소위명이_없으면_NULL_이_정직하다(self):
        """⚠️ **상위 위원회명으로 대신 채우지 마라 — 소위가 아닌 것처럼 보인다.**

        실측: `SUB_COMMITTEE_NAME` 이 52%(8,556/16,342) 비어 있다. 소위인 것은 아는데
        어느 소위인지 원천이 안 준다.
        """
        행 = bills.소위행(
            {"COMMITTEE_NAME": "보건복지위원회", "SUB_COMMITTEE_NAME": None, "SUBMIT_DT": "2025-11-12"}
        )
        assert 행["위원회명"] is None


class TestALLBILL_처리결과:
    """비법률안은 `TVBPMBILL11` 에 없어 본회의 결과를 `ALLBILL.RGS_CONF_RSLT` 만 안다.

    실측(2026-09-11, 표본 39건): 법률안 20건은 `TVBPMBILL11.PROC_RESULT_CD` 와 전부 같았고,
    표결집계가 있는 비법률안 8건은 DB 가 NULL 인데 원천은 '원안가결'·'수정가결' 이었다.
    """

    본체 = {
        "BILL_ID": "PRC_ZZZ", "BILL_NO": "2200051", "BILL_KND": "결산",
        "BILL_NM": "2023회계연도 결산", "PPSR_KND": "정부", "PPSL_DT": "2024-05-31",
        "JRCMIT_NM": "예산결산특별위원회", "RGS_CONF_RSLT": "원안가결",
        "RGS_RSLN_DT": "2024-11-14",
    }

    def test_의안행이_본회의_결과를_담는다(self):
        assert bills.allbill_의안행(self.본체)["처리결과"] == "원안가결"

    def test_아직_본회의_전이면_NULL(self):
        assert bills.allbill_의안행({**self.본체, "RGS_CONF_RSLT": None})["처리결과"] is None

    def test_보완이_본회의를_마친_비법률안을_다시_부른다(self, conn):
        """⚠️ **의안종류가 채워진 비법률안은 종전 조건으로는 다시 안 불렸다.** 첫 수집 뒤에
        본회의를 통과해도 처리결과가 영구히 NULL — 174건이 표결집계까지 있으면서 「계류」로
        읽혔다. 처리결과가 없는 비법률안은 다시 물어야 한다."""
        from congress import db

        db.upsert_위원회(conn, "예산결산특별위원회")
        db.upsert_의안(conn, "ALLBILL", {
            "의안번호": "2200051", "의안ID": "PRC_ZZZ", "의안명": "2023회계연도 결산",
            "의안종류": "결산", "제안자구분": "정부", "제안일": "2024-05-31",
            "소관위원회": "예산결산특별위원회",
        })

        class 원천:
            def json(self, api, **kw):
                assert api == bills.통합_API
                if kw.get("BILL_NO") == "2200051":
                    return [TestALLBILL_처리결과.본체], 1
                return [], 0

        bills.collect_보완(conn, 원천(), 번호들={"2200051"}, 로그=lambda _: None)
        assert conn.execute(
            "SELECT 처리결과 FROM 의안 WHERE 의안번호='2200051'").fetchone()[0] == "원안가결"

    def _원천(self, 응답):
        class 원천:
            호출 = []

            def json(self, api, **kw):
                assert api == bills.통합_API
                self.호출.append(kw.get("BILL_NO"))
                return (응답, 1) if kw.get("BILL_NO") == "2200051" else ([], 0)

        return 원천()

    def test_법률안의_처리결과는_ALLBILL_이_덮지_않는다(self, conn):
        """정본은 `TVBPMBILL11` 이다. 원천이 비어 온 날 가결이 NULL 로 되돌아가면 에러가 안 난다."""
        from congress import db

        db.upsert_위원회(conn, "정무위원회")
        db.upsert_의안(conn, "TVBPMBILL11", {
            "의안번호": "2200051", "의안ID": "PRC_ZZZ", "의안명": "x", "의안종류": "법률안",
            "제안자구분": "의원", "제안일": "2024-05-31", "소관위원회": "정무위원회",
            "처리결과": "원안가결",
        })
        for 값 in (None, "수정가결"):
            bills.collect_보완(conn, self._원천([{**self.본체, "BILL_KND": "법률안", "RGS_CONF_RSLT": 값}]),
                            번호들=set(), 로그=lambda _: None)
            assert conn.execute(
                "SELECT 처리결과 FROM 의안 WHERE 의안번호='2200051'").fetchone()[0] == "원안가결"

    def test_가결된_비법률안은_다시_부르지_않는다(self, conn):
        """공포는 법률안에만 있다 — 「가결인데 공포 없음」을 비법률안까지 걸면 결의안이 매일 불린다."""
        from congress import db

        db.upsert_위원회(conn, "예산결산특별위원회")
        db.upsert_의안(conn, "ALLBILL", {
            "의안번호": "2200051", "의안ID": "PRC_ZZZ", "의안명": "2023회계연도 결산",
            "의안종류": "결산", "제안자구분": "정부", "제안일": "2024-05-31", "처리결과": "원안가결",
        })
        원천 = self._원천([self.본체])
        bills.collect_보완(conn, 원천, 번호들=set(), 로그=lambda _: None)
        assert "2200051" not in 원천.호출
