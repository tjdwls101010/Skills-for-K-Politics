"""회의록 본문 표본이 한 점에 몰려 있지 않은가.

⚠️ **표본 하나가 안 건드리는 원천 변화는 아무도 못 본다.** V10~V14 는 회의록 본문의 구조를
재는 일곱 판정인데 전부 `57024` 하나로만 돌았다 — 그 회의는 **상임위원회**다. 국회가
국정감사 회의록만 새 구조로 바꾸면 일곱이 전부 초록인 채 국정감사 발언이 통째로 깨진다.

실측(2026-08-28)으로 그 위험이 이미 눈에 보인다: `class_id` 가 회의 종류마다 다르다
(상임위 2 · 특위 3 · 예결위 4 · 국정감사 5). 한 표본으로는 **네 값 중 하나만** 밟는다.
(본회의는 D4 로 일부러 안 담으므로 여기 없다.)

고칠 것은 "표본을 흩어라"라는 지침이 아니라 구조다 — 표본을 리터럴이 아니라 **데이터**로
두면 종류를 늘리는 데 코드가 필요 없어지고, 그러면 지침 자체가 필요 없다.
"""

import pytest

import verify


class Test본문표본이_데이터다:
    def test_담는_네_종류를_전부_덮는다(self):
        """⚠️ **`회의` 에 실제로 담기는 종류는 넷이다**(실측: 상임위원회 1,522 ·
        국정감사 317 · 특별위원회 115 · 예산결산특별위원회 69). 하나라도 표본이 없으면
        그 종류의 본문 구조 변화는 진단에 안 걸린다."""
        종류들 = {종류 for _, 종류, _ in verify.본문표본}
        assert 종류들 == {"상임위원회", "국정감사", "특별위원회", "예산결산특별위원회"}

    def test_표본마다_근거가_붙어_있다(self):
        """근거 없는 표본은 왜 그것인지 아무도 몰라 바꾸지도 못한다."""
        assert all(설명.strip() for _, _, 설명 in verify.본문표본)

    def test_첫_표본이_기존_것이다(self):
        """V10~V14 의 판정값이 계획 문서(`01-원천-실측.md`)와 이어지려면 대표 표본이
        바뀌면 안 된다. 늘리는 것은 안전하고, 바꾸는 것은 문서를 함께 고칠 일이다."""
        assert verify.본문표본[0][0] == 57024


class Test본문신호:
    """`본문신호()` 는 HTML 에서 구조 신호만 뽑는다 — 판정은 부르는 쪽이 한다.
    그래서 네트워크 없이 여기서 잴 수 있다."""

    _좋은 = (
        "<html><script>const mnts_id = 55594; const class_id = 5;</script>"
        "<div class='minutes_header'><h1>제418회</h1></div>"
        "<div class='minutes_body'><p class='tit_sm'>가</p>"
        "<div id='spk_1' data-mem_id='0'>ㄱ</div>"
        "<div id='spk_2' data-mem_id='12345'>ㄴ</div></div></html>"
    )

    def test_정상_본문에서_신호를_전부_뽑는다(self):
        s = verify.본문신호(self._좋은)
        assert s["mnts_id"] == 55594 and s["class_id"] == 5
        assert s["블록"] == 2 and s["비의원"] == 1 and s["헤더h1"] is True
        assert s["mem_id없음"] == 0

    def test_구조가_바뀌면_신호가_비어_돌아온다(self):
        """⚠️ **예외로 죽으면 나머지 표본의 답도 못 듣는다.** 한 종류가 깨진 것과
        전부를 모르는 것은 다르다."""
        s = verify.본문신호("<html><body>구조가 통째로 다르다</body></html>")
        assert s["mnts_id"] is None and s["블록"] == 0 and s["헤더h1"] is False

    def test_data_mem_id_가_빠진_블록을_센다(self):
        s = verify.본문신호(
            "<div id='spk_1'>ㄱ</div><div id='spk_2' data-mem_id='0'>ㄴ</div>")
        assert s["블록"] == 2 and s["mem_id없음"] == 1


class Test표본판정:
    """`표본성함()` — 한 표본이 성한가. V10~V14 가 재는 것과 같은 조건이라
    새 종류를 표에 한 줄 더하면 그 종류도 자동으로 같은 잣대를 받는다."""

    def test_성한_표본은_통과한다(self):
        신호 = verify.본문신호(Test본문신호._좋은)
        assert verify.표본성함(55594, 신호) == []

    def test_요청과_다른_id_가_오면_잡는다(self):
        """오배송은 실측된 원천 결함이다 — 200 OK 로 남의 회의록이 온다."""
        신호 = verify.본문신호(Test본문신호._좋은)
        assert any("mnts_id" in 이유 for 이유 in verify.표본성함(99999, 신호))

    def test_비의원이_하나도_없으면_잡는다(self):
        """의원/비의원 판별의 근거가 그 값이라, 안 섞여 오면 판별이 통째로 무너진다."""
        신호 = dict(verify.본문신호(Test본문신호._좋은), 비의원=0)
        assert any("비의원" in 이유 for 이유 in verify.표본성함(55594, 신호))

    def test_요청한_종류와_class_id_가_어긋나면_잡는다(self):
        """⚠️ **표본을 종류마다 둬 놓고 종류를 안 재면 아무것도 안 늘어난다.**
        국정감사 id 로 물었는데 상임위 본문(class_id=2)이 오면 그건 오배송이거나 원천이
        분류를 바꾼 것인데, 구조 신호는 전부 정상이라 **V22 가 초록이다.** 수집기는 이걸
        '막힘' 으로 남긴다(`meetings.py` 회의종류표 교차확인) — 진단도 같은 것을 물어야 한다."""
        신호 = verify.본문신호(Test본문신호._좋은)     # class_id=5 = 국정감사
        assert verify.표본성함(55594, 신호, "국정감사") == []
        이유 = verify.표본성함(55594, 신호, "상임위원회")
        assert any("class_id" in x for x in 이유), 이유

    def test_모르는_class_id_는_안_잡는다(self):
        """⚠️ 원천이 표에 없는 종류를 새로 만들면 그건 **보고할 변화지 빨간불이 아니다** —
        여기서 멈추면 수집이 통째로 건너뛰어진다."""
        신호 = dict(verify.본문신호(Test본문신호._좋은), class_id=99)
        assert not any("class_id" in x for x in verify.표본성함(55594, 신호, "국정감사"))

    def test_블록이_0이면_잡는다(self):
        신호 = verify.본문신호("<html></html>")
        assert verify.표본성함(1, 신호), "빈 본문이 통과하면 표본이 아무것도 안 지킨다"


class Test오배송을_재시도로_가른다:
    """⚠️ **원천은 같은 URL 에 남의 회의록을 준다** — 캐시 계층 문제로 보이고, 옛
    프로젝트에서 2,047건 중 28건이 그랬다. 실측(2026-08-28) 56891 을 네 번 부르니 한 번은
    52486(다른 회의 · class 2)이 왔다.

    그래서 오배송을 그대로 빨간불로 두면 **원천이 안 바뀐 날에도 무작위로 빨개진다.**
    거짓 경고는 그 자체로 신호를 죽인다 — 이 레포는 이미 그 병으로 빨간불 6일을 잃었다.
    받는 자리에서 다시 부르고, **몇 번 어긋났는지는 세어서 남긴다.**
    """

    class _원천:
        """`net.Client` 의 `text` 만 흉내낸다 — 정해둔 mnts_id 를 순서대로 준다."""

        def __init__(self, 줄것):
            self.줄것 = list(줄것)
            self.호출 = 0

        def text(self, url, params=None):
            self.호출 += 1
            return f"<script>const mnts_id = {self.줄것.pop(0)}; const class_id = 2;</script>"

    def test_한_번에_맞으면_다시_안_부른다(self):
        c = self._원천([700])
        _, 어긋난 = verify.본문받기(c, 700)
        assert c.호출 == 1 and 어긋난 == []

    def test_오배송이면_다시_부르고_받은_값을_남긴다(self):
        """⚠️ **횟수만으로는 못 고친다.** 무엇이 왔는지가 있어야 원천의 캐시가 어떤
        회의를 겹쳐 주는지 다음 사람이 쫓을 수 있다."""
        c = self._원천([52486, 55329, 700])
        html, 어긋난 = verify.본문받기(c, 700)
        assert 어긋난 == [52486, 55329]
        assert verify.본문신호(html)["mnts_id"] == 700

    def test_끝내_안_오면_마지막_것을_돌려준다(self):
        """⚠️ **여기서 예외를 내면 나머지 표본의 답도 못 듣는다.** 판정은 부르는 쪽 몫이다."""
        c = self._원천([1, 2, 3])
        html, 어긋난 = verify.본문받기(c, 700, 시도=3)
        assert c.호출 == 3 and 어긋난 == [1, 2, 3]
        assert verify.본문신호(html)["mnts_id"] == 3
