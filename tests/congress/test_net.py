"""`net.py` 의 순수 함수 — 응답 봉투 해석.

**이 도메인의 실패는 예외가 아니라 200 OK 에 빈 결과다.** 국회 API 는 데이터가 없을 때도,
키가 틀렸을 때도, 파라미터 이름이 틀렸을 때도 200 을 주고 본문으로만 구별된다.
그 구별을 한 함수에 모아 두고 여기서 못박는다.
"""

import pytest

import net

정상 = {
    "TVBPMBILL11": [
        {"head": [{"list_total_count": 20019}, {"RESULT": {"CODE": "INFO-000"}}]},
        {"row": [{"BILL_NO": "2220544"}, {"BILL_NO": "2220543"}]},
    ]
}
데이터없음 = {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}
인증오류 = {"RESULT": {"CODE": "INFO-300", "MESSAGE": "인증키가 유효하지 않습니다."}}
마지막페이지 = {
    "TVBPMBILL11": [
        {"head": [{"list_total_count": 20019}, {"RESULT": {"CODE": "INFO-000"}}]},
        {"row": [{"BILL_NO": "2200001"}]},
    ]
}


class Test봉투:
    def test_정상응답에서_행과_총건수를_뽑는다(self):
        rows, total = net.unwrap(정상, "TVBPMBILL11")
        assert total == 20019
        assert [r["BILL_NO"] for r in rows] == ["2220544", "2220543"]

    def test_INFO_200_은_빈_결과지_실패가_아니다(self):
        """⚠️ 이걸 실패로 기록하면 **유령 재시도가 쌓인다.**

        아직 존재하지 않는 의안번호가 정상적으로 이렇게 답한다. 차집합 525건을 훑을 때
        결번이 섞여 있는 것이 정상이고, 국정감사 회의의 의안목록도 이 코드로 온다.
        """
        rows, total = net.unwrap(데이터없음, "TVBPMBILL11")
        assert rows == []
        assert total == 0

    def test_그_밖의_오류코드는_예외다(self):
        """`INFO-200` 만 정상이다. 나머지를 빈 결과로 삼키면 **인증이 끊긴 날 조용히
        0건을 수집하고 초록불로 끝난다.**"""
        with pytest.raises(net.APIError) as e:
            net.unwrap(인증오류, "TVBPMBILL11")
        assert "INFO-300" in str(e.value)

    def test_API_이름이_달라도_찾는다(self):
        """응답의 최상위 키가 곧 API 이름인데, 대소문자나 별칭이 어긋나는 경우가 있다.

        키를 하드코딩으로 맞추려 들면 그 API 하나가 조용히 0건이 된다 —
        `RESULT` 가 아닌 유일한 키를 집는 편이 안전하다.
        """
        rows, _ = net.unwrap(정상, "이름이달라도")
        assert len(rows) == 2

    def test_row_가_없는_응답은_빈_결과다(self):
        """`head` 만 오고 `row` 가 없는 응답이 실재한다(마지막 페이지 경계)."""
        rows, total = net.unwrap({"X": [{"head": [{"list_total_count": 0}]}]}, "X")
        assert rows == []


class Test페이지:
    def test_총건수와_페이지크기로_페이지_수를_센다(self):
        assert net.page_count(20019, 1000) == 21
        assert net.page_count(1000, 1000) == 1
        assert net.page_count(1001, 1000) == 2
        assert net.page_count(0, 1000) == 0

    def test_페이지크기_상한이_1000_이다(self):
        """원천이 `INFO-336`(한 번에 최대 1,000건)을 준다. 넘겨서 배우면 그 요청이 낭비다."""
        with pytest.raises(ValueError, match="1000"):
            net.page_count(5000, 2000)


class Test인증키:
    def test_env_파일에서_읽는다(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
        (tmp_path / ".env").write_text("CONGRESS_API_KEY=abc123\n", encoding="utf-8")
        assert net.load_key(tmp_path / ".env") == "abc123"

    def test_환경변수가_env_파일을_이긴다(self, tmp_path, monkeypatch):
        """자동화가 키를 주입하는 유일한 수단이다."""
        monkeypatch.setenv("CONGRESS_API_KEY", "from-env")
        (tmp_path / ".env").write_text("CONGRESS_API_KEY=from-file\n", encoding="utf-8")
        assert net.load_key(tmp_path / ".env") == "from-env"

    def test_키가_없으면_요청_전에_터진다(self, tmp_path, monkeypatch):
        """키 없이 보내면 200 에 `INFO-300` 이 온다 — 요청을 다 돌고 나서 알게 된다."""
        monkeypatch.delenv("CONGRESS_API_KEY", raising=False)
        with pytest.raises(RuntimeError, match="CONGRESS_API_KEY"):
            net.load_key(tmp_path / "없는.env")


class 가짜시계:
    """`time.monotonic` 자리에 꽂는다. 테스트가 실제로 15초를 기다리지 않게 한다."""

    def __init__(self):
        self.지금 = 1000.0

    def __call__(self) -> float:
        return self.지금


class 가짜클라이언트:
    def __init__(self, 요청수=0, backoff_횟수=0):
        self.요청수, self.backoff_횟수 = 요청수, backoff_횟수


class Test진행자:
    """⚠️ **진행은 시간 기준이지 건수 기준이 아니다.**

    "N건마다" 는 한 건이 느린 패스에서 몇 분씩 조용하다. 그러면 Actions 로그만 보고는
    **러너가 죽은 것과 도는 것을 구별할 수 없다** — 실측으로 국회 수집이 세 번 러너
    통신 두절로 죽는 동안 로그가 210초에 19줄이었다. 그리고 러너가 죽으면
    `upload-artifact` 가 403 으로 실패해 파일 로그는 통째로 사라지므로,
    **stdout 으로 흘린 것만 남는다.**
    """

    def 만든다(self, **kw):
        찍힌것 = []
        시계 = 가짜시계()
        진행 = net.진행자(찍힌것.append, "의안", 시계=시계, **kw)
        return 찍힌것, 시계, 진행

    def test_간격_전에는_찍지_않는다(self):
        찍힌것, 시계, 진행 = self.만든다()
        진행(1)
        시계.지금 += 14.9
        진행(2_000)
        assert 찍힌것 == []

    def test_간격이_지나면_찍는다(self):
        찍힌것, 시계, 진행 = self.만든다()
        시계.지금 += 15.1
        진행(2_000)
        assert len(찍힌것) == 1
        assert "[의안]" in 찍힌것[0] and "2,000" in 찍힌것[0]

    def test_찍은_뒤에는_다시_간격을_기다린다(self):
        찍힌것, 시계, 진행 = self.만든다()
        시계.지금 += 16
        진행(1)
        시계.지금 += 5
        진행(2)
        assert len(찍힌것) == 1

    def test_전체를_알면_분모를_붙인다(self):
        찍힌것, 시계, 진행 = self.만든다(전체=20_084)
        시계.지금 += 16
        진행(12_000)
        assert "12,000/20,084" in 찍힌것[0]

    def test_경과와_요청수와_백오프를_함께_찍는다(self):
        """**현재 건수가 안 움직여도 요청수는 움직인다.** 회의 본문의 slug 폴백처럼
        한 건 안에서 요청을 수십 번 쓰는 구간이 그렇고, 거기서 살아 있다는 증거는
        요청수뿐이다."""
        c = 가짜클라이언트(요청수=4_210, backoff_횟수=2)
        찍힌것, 시계, 진행 = self.만든다(c=c)
        시계.지금 += 20
        진행(100)
        줄 = 찍힌것[0]
        assert "20초" in 줄 and "요청 4,210" in 줄 and "백오프 2" in 줄


class Test목록진행:
    """⚠️ **가장 긴 침묵은 본문 루프가 아니라 목록 전량 조회다.**

    실측(2026-08-19 법령 수집 7분 18초): `── 판례 ──` 00:19:07 → `목록 88,257건`
    00:21:25 사이 **2분 18초 동안 한 줄도 안 나왔다.** 본문 루프에만 진행을 달면
    이 구간이 그대로 조용하고, 하루 1회 자동 수집에서 그 침묵이 곧 "죽었나?" 다.
    """

    class 몸통:
        """`all_pages` 를 실제 코드 그대로 태우는 최소 self.

        `net.Client()` 를 만들면 `http2=True` 때문에 `h2` 가 필요해진다 — 순수
        페이지네이션 로직을 재는 데 HTTP 스택을 세울 이유가 없다.
        """

        all_pages = net.Client.all_pages

        def __init__(self, 총건수, 로그=None, 페이지크기=1000):
            self.총건수, self.로그, self.페이지크기 = 총건수, 로그, 페이지크기
            self.요청수 = self.backoff_횟수 = 0
            self.남은 = 총건수

        def json(self, api, **kw):
            줄 = min(self.페이지크기, self.남은)
            self.남은 -= 줄
            self.요청수 += 1
            return [{"BILL_NO": str(i)} for i in range(줄)], self.총건수

    def test_페이지를_넘길_때마다_살아_있음을_알린다(self, monkeypatch):
        monkeypatch.setattr(net, "진행간격", 0.0)
        찍힌것 = []
        c = self.몸통(20_084, 로그=찍힌것.append)
        assert len(list(c.all_pages("TVBPMBILL11"))) == 20_084
        assert 찍힌것, "목록 조회 구간이 통째로 조용하면 안 된다"
        assert all("목록 TVBPMBILL11" in 줄 for 줄 in 찍힌것)
        assert "20,084" in 찍힌것[-1], "분모가 있어야 얼마나 남았는지 읽을 수 있다"

    def test_로그를_안_주면_아무것도_찍지_않는다(self, monkeypatch):
        """기본은 조용하다 — 라이브러리 호출과 테스트가 stdout 을 더럽히지 않는다."""
        monkeypatch.setattr(net, "진행간격", 0.0)
        c = self.몸통(20_084)
        assert len(list(c.all_pages("TVBPMBILL11"))) == 20_084


class Test불완전한열거는_실패다:
    """⚠️ **총건수가 N 인데 N 행이 안 온 것은 '작은 목록'이 아니라 '실패한 요청'이다.**

    종전에는 조용히 적은 행을 돌려주고 끝났다. 그러면 원천이 절반만 준 날 그 절반이
    **정상적인 전량으로 취급되어** 아래의 교체 가드들(의원위원회·현직·표결)이 근거로 삼는
    "이 목록이 원천의 현재 전부다"라는 전제가 무너진다. `all_pages` 자신이 거부하면
    열 곳 남짓한 모든 목록 호출이 옵트인 없이 한꺼번에 보호된다.
    """

    class 부족한원천:
        all_pages = net.Client.all_pages

        def __init__(self, 총건수, 실제, 페이지크기=1000):
            self.총건수, self.남은, self.페이지크기 = 총건수, 실제, 페이지크기
            self.요청수 = self.backoff_횟수 = 0
            self.로그 = None

        def json(self, api, **kw):
            줄 = min(self.페이지크기, self.남은)
            self.남은 -= 줄
            return [{"BILL_NO": str(i)} for i in range(줄)], self.총건수

    def test_모자라게_오면_예외다(self):
        c = self.부족한원천(20_084, 10_000)
        with pytest.raises(net.APIError, match="20,084"):
            list(c.all_pages("TVBPMBILL11"))

    def test_한_행만_모자라도_예외다(self):
        """'거의 다 왔으니 괜찮다'는 판단을 여기서 하지 않는다 — 어느 행이 빠졌는지
        모르는 채로 넘어가면 그 한 행은 영영 안 온다."""
        c = self.부족한원천(5, 4, 페이지크기=1000)
        with pytest.raises(net.APIError):
            list(c.all_pages("TVBPMBILL11"))

    def test_넘치게_와도_예외다(self):
        c = self.부족한원천(5, 9, 페이지크기=1000)
        with pytest.raises(net.APIError):
            list(c.all_pages("TVBPMBILL11"))

    def test_딱_맞으면_조용하다(self):
        c = self.부족한원천(20_084, 20_084)
        assert len(list(c.all_pages("TVBPMBILL11"))) == 20_084

    def test_총건수_0_은_정상이다(self):
        """INFO-200 이 `([], 0)` 으로 온다. 그건 실패가 아니라 '없다'는 답이다."""
        c = self.부족한원천(0, 0)
        assert list(c.all_pages("TVBPMBILL11")) == []


class Test상태코드_분류:
    """⚠️ **4xx 전체를 '원천에 없음' 으로 읽으면 안 된다.** 이 원천은 UA 가 빠져도, 인증이
    틀려도 400 을 준다(실측 `Bad Request.`). 그것을 '없음' 으로 원장에 남기면 **살아 있는
    자료가 영구히 「원천에 없다」로 굳고 감사가 그걸 구멍에서 제외한다.** 「없다」는 답은
    404 뿐이고(INFO-200 은 봉투가 따로 안다), 나머지 4xx 는 5xx 처럼 재시도 뒤 실패다.
    """

    class 가짜응답:
        def __init__(self, status):
            self.status_code, self.request, self.text = status, None, ""

        def raise_for_status(self):
            pass

    class 몸통:
        _보낸다 = net.Client._보낸다

        def __init__(self, 상태들, retries=3):
            self.상태들, self.호출 = list(상태들), 0
            self.pacer, self.retries = net.Pacer(0), retries
            self.요청수 = self.backoff_횟수 = 0
            self._c = self

        def request(self, method, url, **kw):
            self.호출 += 1
            return Test상태코드_분류.가짜응답(self.상태들.pop(0))

    @pytest.fixture(autouse=True)
    def 안기다린다(self, monkeypatch):
        monkeypatch.setattr(net.time, "sleep", lambda _: None)

    def test_404_는_원천에없음이고_재시도하지_않는다(self):
        c = self.몸통([404, 200])
        with pytest.raises(net.원천에없음):
            c._보낸다("GET", "u")
        assert c.호출 == 1

    def test_400_은_재시도_뒤_일반_실패다(self):
        c = self.몸통([400, 400, 400])
        with pytest.raises(net.APIError) as e:
            c._보낸다("GET", "u")
        assert not isinstance(e.value, net.원천에없음)
        assert c.호출 == 3

    def test_400_뒤_200_이면_성공이다(self):
        c = self.몸통([400, 200])
        assert c._보낸다("GET", "u").status_code == 200
        assert c.호출 == 2

    def test_403_도_없음이_아니다(self):
        c = self.몸통([403, 403, 403])
        with pytest.raises(net.APIError) as e:
            c._보낸다("GET", "u")
        assert not isinstance(e.value, net.원천에없음)

    def test_5xx_는_종전대로_재시도다(self):
        c = self.몸통([503, 200])
        assert c._보낸다("GET", "u").status_code == 200
        assert c.호출 == 2
