"""`bills.py` — 발의자 · 표결 · 대안의 순수 파싱."""

import pytest

import bills


class Test발의자:
    def test_대표와_공동을_쉼표로_쪼갠다(self):
        쌍, ok = bills.parse_proposers(
            {
                "BILL_NO": "2220544",
                "PROPOSER": "이해식의원 등 10인",
                "RST_MONA_CD": "CCU1009B",
                "PUBL_MONA_CD": "NUB4941Z,HXT76735,1S05899F,CK143054,HVV5250J,"
                "XR24352K,5765661K,A1B2C3D4,E5F6G7H8",
            }
        )
        assert ok
        assert len(쌍) == 10
        assert 쌍[0] == ("CCU1009B", "대표발의")
        assert all(역할 == "공동발의" for _, 역할 in 쌍[1:])

    def test_대표발의가_여럿일_수_있다(self):
        """S10. ⚠️ **대표발의가 한 명이라고 가정하지 마라 — 공동대표발의가 실재한다.**

        첫 번째만 대표로 잡으면 `역할='대표발의'` 가 2명 이상인 의안이 **0건이 되고,
        0건은 에러가 아니라 그대로 답이 된다.**
        """
        쌍, ok = bills.parse_proposers(
            {
                "BILL_NO": "1",
                "PROPOSER": "최형두의원ㆍ이준석의원ㆍ황정아의원 등 11인",
                "RST_MONA_CD": "A0P4646G,QWL7778X,T6A6557F",
                "PUBL_MONA_CD": "a,b,c,d,e,f,g,h",
            }
        )
        assert ok
        assert sum(1 for _, r in 쌍 if r == "대표발의") == 3
        assert len(쌍) == 11

    def test_등_N인_과_코드_수가_어긋나면_저장하지_않는다(self):
        """⚠️ **정합성 검사는 수집 시점에만 가능하다.**

        DB 에 표시용 제안자 문자열(`'이해식의원 등 10인'`)을 담지 않으므로 **사후 감사로는
        대조할 대상이 없다.** 여기서 놓치면 영영 모른다.
        """
        쌍, ok = bills.parse_proposers(
            {"BILL_NO": "1", "PROPOSER": "누구의원 등 10인", "RST_MONA_CD": "A", "PUBL_MONA_CD": "b,c"}
        )
        assert not ok

    def test_등_N인_이_없는_형식은_검사를_건너뛴다(self):
        """'홍길동의원' 처럼 단독 발의는 '등 N인' 이 없다."""
        쌍, ok = bills.parse_proposers(
            {"BILL_NO": "1", "PROPOSER": "홍길동의원", "RST_MONA_CD": "A", "PUBL_MONA_CD": ""}
        )
        assert ok and 쌍 == [("A", "대표발의")]

    def test_같은_사람이_대표이면서_공동이면_대표가_이긴다(self):
        """PK 가 (의안번호, 의원코드) 라 역할은 키가 아니다 — 둘 중 하나를 골라야 한다."""
        쌍, _ = bills.parse_proposers(
            {"BILL_NO": "1", "PROPOSER": "A의원 등 2인", "RST_MONA_CD": "A", "PUBL_MONA_CD": "A,B"}
        )
        assert dict(쌍)["A"] == "대표발의"


class Test표결:
    def test_집계행을_만든다(self):
        assert bills.표결집계행(
            {
                "BILL_NO": "2220257", "PROC_DT": "2026-07-31", "MEMBER_TCNT": 299,
                "VOTE_TCNT": 178, "YES_TCNT": 175, "NO_TCNT": 2, "BLANK_TCNT": 1,
            }
        ) == {
            "의안번호": "2220257", "의결일": "2026-07-31", "재적수": 299,
            "투표수": 178, "찬성수": 175, "반대수": 2, "기권수": 1,
        }

    def test_명단행을_만든다(self):
        assert bills.표결행({"BILL_NO": "2200851", "MONA_CD": "W8W95421", "RESULT_VOTE_MOD": "찬성"}) == (
            "2200851", "W8W95421", "찬성",
        )

    def test_CHECK_밖_표결결과는_거른다(self):
        """⚠️ **틀린 값이 들어오는 것보다 안 들어오는 것이 낫다.** 그 행만 빼고 실행은 계속된다."""
        assert bills.표결행({"BILL_NO": "1", "MONA_CD": "A", "RESULT_VOTE_MOD": "무효"}) is None


class Test대안:
    HTML = """
    <html><body>
      <a href="billDetailPage.do?billId=X">국회법 일부개정법률안(대안) 2220259</a>
      <table><tbody class="anBill_body">
        <tr><td>2200255</td></tr><tr><td>2200990</td></tr>
        <tr><td>2217711</td></tr><tr><td>2219947</td></tr>
      </tbody></table>
    </body></html>
    """

    def test_자기_번호를_제외한다(self):
        """S11. ⚠️ 응답에 자기 번호가 함께 온다. 안 걸러내면 **대안이 자기를 가리킨다.**"""
        assert bills.parse_anbill(self.HTML, "2220259") == {
            "2200255", "2200990", "2217711", "2219947",
        }

    def test_0건이면_실패다(self):
        """⚠️ **파싱이 0건이면 성공으로 넘기지 마라.** 대안이면 흡수한 원안이 반드시 있다.

        `<tbody class="anBill_body">` 가 비어 올 수 있어서(서버가 채우는 부분과 클라이언트가
        그리는 부분이 섞여 있다) 정규식으로 응답 전체를 긁는데, 그래도 0건이면 실패다.
        """
        with pytest.raises(ValueError, match="0건"):
            bills.parse_anbill("<html><body>없음</body></html>", "2220259")

    def test_21대_번호도_긁는다(self):
        """대안이 21대 의안을 흡수하는 경우가 있을 수 있다 — 정규식은 2[12] 로 연다."""
        assert "2100001" in bills.parse_anbill(
            "<td>2100001</td><td>2220259</td>", "2220259"
        )
