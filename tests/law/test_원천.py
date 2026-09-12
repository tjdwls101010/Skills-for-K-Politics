"""`source.py` — 봉투 해석과 유량.

**여기서 잠그는 것은 전부 "에러 없이 틀리는" 자리다.** 봉투 이름 하나가 어긋나면 그
자료종류만 조용히 0건이 되고, 단수/복수 정규화가 빠지면 개발 중엔 안 터지고 운영에서
터진다.
"""

import threading
import time

import pytest

from law import source as 원천


class Test봉투표:
    """`01-원천-실측.md` §3 의 12개 값. 규칙으로 유도하면 반드시 둘 중 하나에서 틀린다."""

    기대 = {
        "법령": ("law", "LawSearch", "law", "법령"),
        "판례": ("prec", "PrecSearch", "prec", "PrecService"),
        "헌재결정례": ("detc", "DetcSearch", "Detc", "DetcService"),
        "행정심판례": ("decc", "Decc", "decc", "PrecService"),
        "법령해석례": ("expc", "Expc", "expc", "ExpcService"),
        "행정규칙": ("admrul", "AdmRulSearch", "admrul", "AdmRulService"),
    }

    def test_여섯_종이_전부_있다(self):
        assert set(원천.봉투) == set(원천.자료종류.전체) == set(self.기대)

    @pytest.mark.parametrize("종류", 기대)
    def test_봉투와_리스트키가_실측표와_같다(self, 종류):
        i = 원천.봉투[종류]
        assert (i.target, i.목록봉투, i.리스트키, i.본문봉투) == self.기대[종류]

    def test_detc_리스트키만_대문자다(self):
        assert 원천.봉투["헌재결정례"].리스트키 == "Detc"
        assert all(
            i.리스트키 == i.target
            for k, i in 원천.봉투.items()
            if k != "헌재결정례"
        )

    def test_decc_본문봉투가_판례와_같다(self):
        assert 원천.봉투["행정심판례"].본문봉투 == 원천.봉투["판례"].본문봉투 == "PrecService"

    def test_행정심판례만_목록과_본문의_ID_필드명이_다르다(self):
        assert 원천.봉투["행정심판례"].목록식별자 == "행정심판재결례일련번호"
        # 다른 다섯은 목록 필드명이 본문 테이블의 일련번호 이름과 같은 계열이다
        assert 원천.봉투["판례"].목록식별자 == "판례일련번호"

    def test_판례는_datSrcNm_이_기본으로_걸린다(self):
        assert 원천.목록기본["판례"]["datSrcNm"] == "대법원"

    def test_행정규칙은_nw1_이_현행이다(self):
        # ⚠️ eflaw 는 2가 시행예정인데 admrul 은 2가 연혁이다. 뜻이 반대다.
        assert 원천.목록기본["행정규칙"]["nw"] == "1"


class Test단수복수정규화:
    def test_결과가_1건이면_dict_로_온다(self):
        assert 원천.행들({"판례일련번호": "1"}) == [{"판례일련번호": "1"}]

    def test_복수는_그대로(self):
        v = [{"a": 1}, {"a": 2}]
        assert 원천.행들(v) == v

    def test_0건이면_키가_없어_None_이_온다(self):
        assert 원천.행들(None) == []

    def test_dict_아닌_원소는_버린다(self):
        assert 원천.행들([{"a": 1}, "쓰레기", None]) == [{"a": 1}]


class Test봉투분류:
    def test_정상_목록(self):
        p = {"LawSearch": {"totalCnt": "5607", "law": [{"법령ID": "1"}]}}
        assert 원천.분류(p, "법령", "목록")["totalCnt"] == "5607"

    def test_정상_본문(self):
        assert 원천.분류({"법령": {"기본정보": {}}}, "법령", "본문") == {"기본정보": {}}

    def test_헌재_목록은_대문자_Detc_로_온다(self):
        p = {"DetcSearch": {"totalCnt": "38672", "Detc": [{"a": 1}]}}
        몸통 = 원천.분류(p, "헌재결정례", "목록")
        assert 원천.행들(몸통.get(원천.봉투["헌재결정례"].리스트키)) == [{"a": 1}]

    def test_행정심판례_본문은_PrecService_봉투다(self):
        assert 원천.분류({"PrecService": {"이유": "…"}}, "행정심판례", "본문") == {"이유": "…"}

    def test_0건이면_리스트키_자체가_없다(self):
        p = {"LawSearch": {"resultMsg": "success", "totalCnt": "0", "target": "law"}}
        몸통 = 원천.분류(p, "법령", "목록")
        assert 원천.행들(몸통.get("law")) == []
        assert int(몸통["totalCnt"]) == 0

    def test_인증실패는_HTTP200_에_다른_봉투로_온다(self):
        p = {"result": "사용자 정보 검증에 실패하였습니다.", "msg": "IP 주소를 등록해 주세요."}
        with pytest.raises(원천.인증실패):
            원천.분류(p, "판례", "본문")

    def test_인증실패는_목록에서도_잡힌다(self):
        with pytest.raises(원천.인증실패):
            원천.분류({"result": "…", "msg": "…"}, "법령", "목록")

    def test_본문_부재는_Law_문자열로_온다(self):
        p = {"Law": "일치하는 판례가 없습니다.  판례명을 확인하여 주십시오."}
        with pytest.raises(원천.원천에없음):
            원천.분류(p, "판례", "본문")

    def test_없음은_재시도_대상이_아니다(self):
        # 분류 체계상 원천에없음 은 API오류 의 하위이고 인증실패 와는 형제다
        assert issubclass(원천.원천에없음, 원천.API오류)
        assert not issubclass(원천.원천에없음, 원천.인증실패)

    def test_모르는_봉투는_삼키지_않고_올린다(self):
        # ⚠️ 빈 결과로 삼키면 형식이 바뀐 날 조용히 0건을 수집하고 초록불로 끝난다.
        with pytest.raises(원천.API오류):
            원천.분류({"엉뚱한봉투": {"a": 1}}, "판례", "목록")

    def test_판례_목록_봉투를_본문으로_읽으면_실패한다(self):
        with pytest.raises(원천.API오류):
            원천.분류({"PrecSearch": {"prec": []}}, "판례", "본문")


class Test유량:
    def test_여러_스레드가_공유해도_유량이_지켜진다(self):
        """⚠️ congress 의 Pacer 는 직렬 전제라 락이 없다. 그대로 베끼면 24개 워커가
        각자 자기 시계를 보고 동시에 나가 유량 제한이 사실상 사라진다."""
        rate = 200.0
        간격 = 1.0 / rate
        p = 원천.Pacer(rate)
        n스레드, n회 = 8, 5
        시각: list[float] = []
        락 = threading.Lock()

        def 돈다():
            for _ in range(n회):
                p.wait()
                with 락:
                    시각.append(time.monotonic())

        스레드들 = [threading.Thread(target=돈다) for _ in range(n스레드)]
        시작 = time.monotonic()
        for t in 스레드들:
            t.start()
        for t in 스레드들:
            t.join()
        총 = n스레드 * n회
        assert len(시각) == 총
        assert time.monotonic() - 시작 >= (총 - 1) * 간격 * 0.9

    def test_rate_0_이면_기다리지_않는다(self):
        p = 원천.Pacer(0)
        시작 = time.monotonic()
        for _ in range(50):
            p.wait()
        assert time.monotonic() - 시작 < 0.05


class Test워커수:
    """손잡이는 `rate` 하나다. 워커 수는 거기서 정해진다."""

    @pytest.mark.parametrize(
        "rate, 기대", [(24.0, 24), (8.0, 8), (1.0, 1), (0.5, 1), (100.0, 24)]
    )
    def test_rate_에서_워커가_정해지고_상한을_넘지_않는다(self, rate, 기대):
        assert 원천.워커수(rate) == 기대

    def test_실측한_상한을_올리지_않는다(self):
        # 24 동시에서 24.15 req/s · 실패 1/96. 그 위는 안 재봤고, 차단당하면 계정 단위다.
        assert 원천.동시상한 == 24


class Test인증키:
    def test_환경변수가_env_파일을_이긴다(self, tmp_path, monkeypatch):
        f = tmp_path / ".env"
        f.write_text("LAW_API_KEY=파일값\n", encoding="utf-8")
        monkeypatch.setenv("LAW_API_KEY", "환경값")
        assert 원천.load_key(f) == "환경값"

    def test_env_파일에서_읽는다(self, tmp_path, monkeypatch):
        monkeypatch.delenv("LAW_API_KEY", raising=False)
        f = tmp_path / ".env"
        f.write_text('# 주석\nLAW_API_KEY="따옴표값"\n', encoding="utf-8")
        assert 원천.load_key(f) == "따옴표값"

    def test_아무것도_없으면_기본값으로_떨어진다(self, tmp_path, monkeypatch):
        """법제처의 OC 는 무료·공개 값이라 커밋해 뒀다. **감출 것이 아니라서다** —
        이미 계획 문서 여러 곳에 있고 레포는 공개다.

        ⚠️ 시크릿으로 두면 감춰지지도 않으면서 **새 기계마다 등록해야 하는 단계와
        "키가 없어 빈손 수집"이라는 실패 유형만 는다.** 셀프호스티드 러너는 자기
        `_work` 아래 새 체크아웃에서 도는데 `.env` 는 gitignore 라 거기 안 딸려 온다.
        """
        monkeypatch.delenv("LAW_API_KEY", raising=False)
        assert 원천.load_key(tmp_path / "없는파일") == 원천.기본OC
        assert 원천.기본OC

    def test_빈_환경변수는_기본값을_덮지_않는다(self, tmp_path, monkeypatch):
        # ⚠️ GitHub Actions 는 등록 안 된 시크릿을 **빈 문자열**로 치환한다.
        #    그걸 그대로 쓰면 인증 실패로 20만 건을 헛되이 두드린다.
        monkeypatch.setenv("LAW_API_KEY", "")
        assert 원천.load_key(tmp_path / "없는파일") == 원천.기본OC


class Test맵:
    def test_전부_처리한다(self):
        assert sorted(원천.맵(lambda x: x * 2, [1, 2, 3, 4, 5], 4)) == [2, 4, 6, 8, 10]

    def test_빈_입력은_아무것도_안_한다(self):
        assert list(원천.맵(lambda x: x, [], 4)) == []

    def test_워커가_1이면_직렬로_돈다(self):
        assert list(원천.맵(lambda x: x, [3, 1, 2], 1)) == [3, 1, 2]

    def test_느린_한_건이_뒤를_붙잡지_않는다(self):
        """`executor.map` 은 입력 순서로 내주므로 첫 건이 느리면 메인 스레드의 DB 쓰기가
        그동안 통째로 멈춘다. 완료 순서로 흘려야 한다."""

        def 일(x):
            time.sleep(0.20 if x == 0 else 0.0)
            return x

        assert next(iter(원천.맵(일, [0, 1, 2, 3], 4))) != 0

    def test_예외는_그대로_올라온다(self):
        def 터진다(x):
            raise ValueError(x)

        with pytest.raises(ValueError):
            list(원천.맵(터진다, [1], 2))


class Test목록전량의_완결_판정:
    """**정리(DELETE)를 켜도 되는가**를 정하는 자리다. 여기가 거짓 초록이면 멀쩡한
    수집물을 지우고, 거짓 빨강이면 정리·승격이 영영 안 돈다 — **둘 다 조용하다.**

    ⚠️ 실제로 거짓 빨강이 났다. 판정이 `목록식별자` 로 고유 건수를 세는데,
    `eflaw&nw=2`(시행예정)는 **한 법의 한 버전을 시행일마다 한 행씩** 준다 —
    실측 1,020행 · MST 고유 937(중복 69종) · `(MST, 시행일자)` 고유 **1,020**.
    행이 중복인 게 아니라 **행을 세는 키가 틀렸던 것**이고, 그 탓에 법령의
    정리·승격이 계속 건너뛰어졌다(감사 A1·A18 이 고칠 수 없는 빨강으로 굳었다).
    """

    class 가짜:
        """`목록페이지` 만 있는 최소 self. `목록전량` 은 그것 말고 아무것도 안 쓴다."""

        def __init__(self, 페이지들, 총):
            self.페이지들, self.총 = 페이지들, 총

        def 목록페이지(self, 종류, p, **params):
            return self.페이지들[p - 1], self.총

    @staticmethod
    def _돌린다(페이지들, 총, **kw):
        return 원천.Client.목록전량(
            Test목록전량의_완결_판정.가짜(페이지들, 총), "법령", **kw
        )

    def _행(self, mst, 시행일):
        return {"법령일련번호": mst, "시행일자": 시행일}

    def test_다_받으면_완결이다(self):
        행들, 완결 = self._돌린다([[self._행("1", "2026-01-01"),
                                   self._행("2", "2026-01-01")]], 2)
        assert len(행들) == 2 and 완결

    def test_덜_받으면_불완결이다(self):
        """⚠️ 이걸 통과시키면 못 받은 행이 '목록에 없다'가 되어 정리가 지운다."""
        _, 완결 = self._돌린다([[self._행("1", "2026-01-01")]], 2)
        assert not 완결

    def test_한_버전이_시행일마다_한_행씩_와도_완결이다(self):
        """실측 — `각급 법원의 설치와 관할구역에 관한 법률` MST 216003 이 세 행이다.
        MST 로 세면 1개라 영원히 불완결이 된다."""
        페이지 = [self._행("216003", "2032-03-01"),
                  self._행("216003", "2029-03-01"),
                  self._행("216003", "2028-03-01")]
        _, 완결 = self._돌린다([페이지], 3, 행키=("법령일련번호", "시행일자"))
        assert 완결

    def test_행키를_안_주면_목록식별자로_센다(self):
        """기본값은 그대로여야 한다 — 나머지 다섯 자료는 목록식별자가 곧 행 키다."""
        페이지 = [self._행("216003", "2032-03-01"), self._행("216003", "2029-03-01")]
        _, 완결 = self._돌린다([페이지], 2)
        assert not 완결

    def test_행키로_봐도_중복이면_불완결이다(self):
        """⚠️ **이게 원래 이 검사가 겨눈 것이다.** 페이지가 흔들려 같은 행을 두 번
        주고 다른 행을 빠뜨리면 행 수는 맞는데 목록은 불완전하다. 행키를 줘도
        그건 여전히 잡혀야 한다 — 안 그러면 정리가 빠진 행을 지운다."""
        페이지 = [self._행("1", "2026-01-01"), self._행("1", "2026-01-01"),
                  self._행("3", "2026-01-01")]
        _, 완결 = self._돌린다([페이지], 3, 행키=("법령일련번호", "시행일자"))
        assert not 완결

    def test_0건은_완결이_아니다(self):
        """⚠️ 원천이 잠깐 빈손으로 올 때 정리를 켜면 **테이블을 통째로 비운다.**"""
        _, 완결 = self._돌린다([[]], 0)
        assert not 완결

    def test_여러_페이지를_이어_붙인다(self):
        페이지들 = [[self._행(str(i), "2026-01-01") for i in range(1000)],
                    [self._행("1000", "2026-01-01")]]
        행들, 완결 = self._돌린다(페이지들, 1001)
        assert len(행들) == 1001 and 완결


class 가짜시계:
    """`time.monotonic` 자리에 꽂는다. 테스트가 실제로 15초를 기다리지 않게 한다."""

    def __init__(self):
        self.지금 = 1000.0

    def __call__(self) -> float:
        return self.지금


class Test진행자:
    """⚠️ **진행은 시간 기준이지 건수 기준이 아니다.**

    법령 수집은 21분을 도는데 워크플로가 로그를 파일로만 받고 있었다 — 화면이 21분간
    비었다. 스트리밍을 고쳐도 "N건마다" 로 찍으면 느린 구간에서 몇 분씩 조용하고,
    그러면 로그만 보고는 **러너가 죽은 것과 도는 것을 구별할 수 없다.**
    """

    class 가짜클라이언트:
        def __init__(self, 요청수=0, backoff_횟수=0):
            self.요청수, self.backoff_횟수 = 요청수, backoff_횟수

    def 만든다(self, **kw):
        찍힌것 = []
        시계 = 가짜시계()
        return 찍힌것, 시계, 원천.진행자(찍힌것.append, "본문", 시계=시계, **kw)

    def test_간격_전에는_찍지_않는다(self):
        찍힌것, 시계, 진행 = self.만든다()
        시계.지금 += 14.9
        진행(2_000)
        assert 찍힌것 == []

    def test_간격이_지나면_분모와_함께_찍는다(self):
        찍힌것, 시계, 진행 = self.만든다(전체=6_495)
        시계.지금 += 15.1
        진행(1_200)
        assert len(찍힌것) == 1
        assert "[본문] 1,200/6,495" in 찍힌것[0]

    def test_찍은_뒤에는_다시_간격을_기다린다(self):
        찍힌것, 시계, 진행 = self.만든다()
        시계.지금 += 16
        진행(1)
        시계.지금 += 5
        진행(2)
        assert len(찍힌것) == 1

    def test_경과와_요청수와_백오프를_함께_찍는다(self):
        c = self.가짜클라이언트(요청수=4_210, backoff_횟수=2)
        찍힌것, 시계, 진행 = self.만든다(c=c)
        시계.지금 += 20
        진행(100)
        assert "20초" in 찍힌것[0] and "요청 4,210" in 찍힌것[0] and "백오프 2" in 찍힌것[0]


class Test목록진행:
    """⚠️ **가장 긴 침묵은 본문 루프가 아니라 목록 전량 조회다.**

    실측(2026-08-19 법령 수집 7분 18초): `── 판례 ──` 00:19:07 → `목록 88,257건`
    00:21:25 사이 **2분 18초 동안 한 줄도 안 나왔다.**
    """

    class 몸통:
        """`목록전량` 을 실제 코드 그대로 태우는 최소 self — httpx 를 열지 않는다."""

        목록전량 = 원천.Client.목록전량

        def __init__(self, 총건수, 로그=None, 페이지크기=원천.최대페이지크기):
            self.총건수, self.로그, self.페이지크기 = 총건수, 로그, 페이지크기
            self.요청수 = self.backoff_횟수 = 0
            self.낸것 = 0

        def 목록페이지(self, 종류, p, **params):
            줄 = min(self.페이지크기, self.총건수 - self.낸것)
            행 = [{"판례일련번호": str(self.낸것 + i)} for i in range(줄)]
            self.낸것 += 줄
            self.요청수 += 1
            return 행, self.총건수

    def test_페이지를_넘길_때마다_살아_있음을_알린다(self, monkeypatch):
        monkeypatch.setattr(원천, "진행간격", 0.0)
        찍힌것 = []
        c = self.몸통(88_257, 로그=찍힌것.append)
        행, 완결 = c.목록전량("판례")
        assert len(행) == 88_257 and 완결
        assert 찍힌것, "2분 18초를 조용히 도는 구간이 남아 있으면 안 된다"
        assert "88,257" in 찍힌것[-1]

    def test_로그를_안_주면_아무것도_찍지_않는다(self, monkeypatch):
        monkeypatch.setattr(원천, "진행간격", 0.0)
        c = self.몸통(1_000)
        행, _ = c.목록전량("판례")
        assert len(행) == 1_000
