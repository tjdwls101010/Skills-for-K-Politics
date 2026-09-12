"""`meetings.py` — 회의 열거 · 회의록 본문 · 발언자 연결."""

import pytest

import meetings

열거행 = [
    {
        "CONFER_NUM": "57072", "CONF_ID": "N054355", "CLASS_NAME": "상임위원회",
        "COMM_NAME": "법제사법위원회 법안심사제1소위원회", "CONF_DATE": "2026-07-30",
        "TITLE": "제22대 제437회 제10차 법제사법위원회 법안심사제1소위원회 (2026년 07월 30일)",
        "SUB_NAME": "1. 형사소송법 일부개정법률안(김용민 의원 대표발의)(의안번호 2219564)",
    },
    {
        "CONFER_NUM": "57072", "CONF_ID": "N054355", "CLASS_NAME": "상임위원회",
        "COMM_NAME": "법제사법위원회 법안심사제1소위원회", "CONF_DATE": "2026-07-30",
        "TITLE": "제22대 제437회 제10차 법제사법위원회 법안심사제1소위원회 (2026년 07월 30일)",
        "SUB_NAME": "2. 간사 선임의 건",
    },
    {
        "CONFER_NUM": "57072", "CONF_ID": "N054355", "CLASS_NAME": "상임위원회",
        "COMM_NAME": "법제사법위원회 법안심사제1소위원회", "CONF_DATE": "2026-07-30",
        "TITLE": "제22대 제437회 제10차 법제사법위원회 법안심사제1소위원회 (2026년 07월 30일)",
        "SUB_NAME": "3. 국무총리 임명동의안`#`(의안번호 2219564)`#` 재상정",
    },
]

감사행 = [
    {
        "CONF_ID": "N053657", "SESS": "제429회", "DGR": "개회식", "CONF_DT": "2025-11-06",
        "CONF_KND": "국정감사 회의록", "CMIT_NM": "국회운영위원회",
        "DOWN_URL": "https://record.assembly.go.kr/assembly/viewer/minutes/download/pdf.do?id=55735",
    }
]


class Test열거:
    def test_TITLE_에서_회기와_차수만_집는다(self):
        """⚠️ **위원회명까지 뽑지 마라.** `COMM_NAME` 이 따로 오고 그쪽이 정본이다.
        정규식이 적게 집을수록 덜 깨진다."""
        회의, _ = meetings.parse_enum(열거행)
        assert len(회의) == 1
        m = 회의[0]
        assert m["회의id"] == 57072
        assert m["회기"] == 437
        assert m["차수"] == 10
        assert m["회의일자"] == "2026-07-30"
        assert m["위원회명"] == "법제사법위원회 법안심사제1소위원회"
        assert m["회의종류"] == "상임위원회"
        assert m["회의코드"] == "N054355"

    def test_행이_회의_단위가_아니라_회의_안건_단위다(self):
        """2026년 9,410행이 실제로는 회의 수백 건이다. `CONFER_NUM` 으로 접어야 한다."""
        회의, 안건 = meetings.parse_enum(열거행)
        assert len(회의) == 1
        assert 안건 == {57072: {"2219564"}}

    def test_의안이_아닌_안건은_버린다(self):
        """'간사 선임의 건' 은 의안번호가 없다 — 이 DB 의 목적에 기여하지 않는다."""
        _, 안건 = meetings.parse_enum([열거행[1]])
        assert 안건 == {}

    def test_강조표시_백틱샵이_끼어도_번호를_뽑는다(self):
        """원천이 `` `#` `` 를 끼워 넣는다: '…임명동의안`#`(의안번호 2219175)`#`'."""
        _, 안건 = meetings.parse_enum([열거행[2]])
        assert 안건 == {57072: {"2219564"}}

    def test_한_회의의_같은_의안은_한_번만_센다(self):
        """실측 N054339: 안건 90개 / 고유 의안 41개. PK 가 (회의id, 의안번호) 다."""
        _, 안건 = meetings.parse_enum(열거행)
        assert 안건[57072] == {"2219564"}


class Test국정감사열거:
    def test_DOWN_URL_에서_회의id_를_뽑는다(self):
        회의 = meetings.parse_감사(감사행)
        assert 회의[0]["회의id"] == 55735
        assert 회의[0]["회의종류"] == "국정감사"
        assert 회의[0]["위원회명"] == "국회운영위원회"
        assert 회의[0]["회의일자"] == "2025-11-06"

    def test_회기는_SESS_에서_오고_차수는_NULL_이다(self):
        """⚠️ **`DGR` 을 차수로 넣지 마라 — '개회식' 처럼 숫자가 아닌 값이 온다.**

        계획 초안은 '국정감사는 회기도 차수도 없다'고 적었는데 그건 **본문 헤더** 기준이다.
        API 는 `SESS` 를 준다.
        """
        회의 = meetings.parse_감사(감사행)
        assert 회의[0]["회기"] == 429
        assert 회의[0]["차수"] is None


MINUTES = """
<html><head><title>국회회의록</title></head><body>
<h1>국회회의록 NATIONAL ASSEMBLY MINUTES</h1>
<script>const mnts_id = 57024; const class_id = 2;</script>
<div class="minutes_header"><h1>정무위원회회의록</h1></div>
<div class="minutes_body">
  <div id="spk_2" class="item0 speaker spk_mem" data-mem_id="6692" data-name="유동수" data-pos="위원장">
    <div class="man"><a href="https://www.assembly.go.kr/members/22nd/YOODONGSOO">
      <span class="pic"><img src="https://www.assembly.go.kr/static/portal/img/openassm/new/thumb/AAA111.PNG"></span>
    </a></div>
    <div class="talk"><div class="txt">
      <span class="spk_sub">첫 문단.</span><br/><span class="spk_sub">둘째 문단.</span>
    </div></div>
  </div>
  <div id="spk_7" class="item0 speaker spk_mem" data-mem_id="0" data-name="김주현" data-pos="금융위원장">
    <div class="man"><span class="pic"><img src="/img/none.png"></span></div>
    <div class="talk"><div class="txt"><span class="spk_sub">답변입니다.</span></div></div>
  </div>
  <div id="spk_9" class="item0 speaker spk_mem" data-mem_id="7001" data-name="柳榮夏" data-pos="위원">
    <div class="man"><a href="https://www.assembly.go.kr/members/22nd/RYUYOUNGHA">
      <span class="pic"><img src="/img/openassm/q129715y.jpg"></span></a></div>
    <div class="talk"><div class="txt"><span class="spk_sub">한자 이름입니다.</span></div></div>
  </div>
</div></body></html>
"""


class Test본문:
    def test_mnts_id_와_class_id_를_읽는다(self):
        mid, cid, _ = meetings.parse_minutes(MINUTES, 57024)
        assert (mid, cid) == (57024, 2)

    def test_요청한_id_와_다르면_거부한다(self):
        """⚠️ **오배송 가드.** 같은 URL 이 요청마다 다른 회의를 줄 수 있다(캐시 계층 문제).

        옛 프로젝트는 2,047건 중 28건이 남의 본문이었다. **대수만 봐서는 못 막는다** —
        틀린 문서도 22대일 수 있다.
        """
        with pytest.raises(ValueError, match="오배송"):
            meetings.parse_minutes(MINUTES, 99999)

    def test_id_를_안_밝히면_저장하지_않는다(self):
        """**'괜찮다'가 아니라 '확인할 수 없다' 이다.** 확인할 수 없는 것을 확인된 것처럼
        저장하는 것이 여기서 가장 위험하다."""
        with pytest.raises(ValueError, match="mnts_id"):
            meetings.parse_minutes("<html>본문만 있고 스크립트가 없다</html>", 57024)

    def test_발언_순서를_등장순으로_다시_매긴다(self):
        """⚠️ `spk_N` 은 오름차순이지만 **연속이 아니고** 전부 `spk_2` 에서 시작한다."""
        _, _, 발언 = meetings.parse_minutes(MINUTES, 57024)
        assert [s["순서"] for s in 발언] == [1, 2, 3]

    def test_비의원_판별은_data_mem_id_다(self):
        """⚠️ **`class` 의 `spk_mem` 을 판별자로 쓰지 마라 — 비의원 블록도 그걸 단다.**

        여기 속으면 비의원을 전부 의원으로 저장한다(실측 780개 중 278개가 비의원).
        """
        _, _, 발언 = meetings.parse_minutes(MINUTES, 57024)
        assert [s["의원인가"] for s in 발언] == [True, False, True]
        assert 발언[1]["직위"] == "금융위원장"

    def test_여러_문단을_개행으로_이어_붙인다(self):
        _, _, 발언 = meetings.parse_minutes(MINUTES, 57024)
        assert 발언[0]["내용"] == "첫 문단.\n둘째 문단."

    def test_이름과_직위는_파싱_없이_온다(self):
        _, _, 발언 = meetings.parse_minutes(MINUTES, 57024)
        assert (발언[0]["발언자명"], 발언[0]["직위"]) == ("유동수", "위원장")


class Test사진키:
    def test_경로가_아니라_basename_으로_비교한다(self):
        """⚠️ 회의록 img 경로가 3종이다 — `new/thumb/`, `new/`, `openassm/{코드}.jpg`."""
        assert meetings.사진키("https://x/openassm/new/thumb/a3a3.png") == "a3a3"
        assert meetings.사진키("https://x/openassm/new/a3a3.png") == "a3a3"

    def test_확장자_대문자를_소문자로_맞춘다(self):
        """⚠️ `.JPG` 가 섞인다. 마지막 '.' 기준으로 자르고 소문자화한다."""
        assert meetings.사진키("/x/AAA111.PNG") == "aaa111"

    def test_해시라고_가정하지_않는다(self):
        """⚠️ 22대 320명 중 25명은 `NAAS_PIC` 이 `{의원코드}.jpg` 형태다(`q129715y.jpg`).

        `[0-9a-f]{32}` 정규식을 쓰면 그 25명이 통째로 빠진다.
        """
        assert meetings.사진키("/img/openassm/q129715y.jpg") == "q129715y"


class Test발언자연결:
    사진색인 = {"aaa111": "GX38539O"}

    def test_사진_basename_으로_잇는다(self):
        발언 = {"의원인가": True, "사진키": "aaa111", "slug": "YOODONGSOO", "발언자명": "유동수"}
        assert meetings.resolve_의원코드(발언, self.사진색인, {"YOODONGSOO": "ZZZ"}) == "GX38539O"

    def test_사진이_실패하면_slug_로_넘어간다(self):
        """⚠️ **①만으로는 실패율 21%다**(실측 61명 중 13명). www 쪽 사진이 갱신되면
        회의록에 박힌 옛 파일명과 어긋난다. **②를 '있으면 좋은 폴백'으로 미루지 마라** —
        미루면 그 21% 의원의 모든 발언이 NULL 로 들어가고 시나리오 3이 조용히 실패한다.
        """
        발언 = {"의원인가": True, "사진키": "없는키", "slug": "RYUYOUNGHA", "발언자명": "柳榮夏"}
        assert meetings.resolve_의원코드(발언, self.사진색인, {"RYUYOUNGHA": "QQ11111Q"}) == "QQ11111Q"

    def test_파일명이_코드형이면_그_자체가_의원코드다(self):
        발언 = {"의원인가": True, "사진키": "q129715y", "slug": None, "발언자명": "가"}
        assert meetings.resolve_의원코드(발언, {"q129715y": "Q129715Y"}, {}) == "Q129715Y"

    def test_비의원은_언제나_NULL_이다(self):
        발언 = {"의원인가": False, "사진키": "aaa111", "slug": None, "발언자명": "김주현"}
        assert meetings.resolve_의원코드(발언, self.사진색인, {}) is None

    def test_둘_다_실패하면_NULL_이고_이름으로_추측하지_않는다(self):
        """⚠️ **이름으로 추측해 채우지 마라. 22대에 박지원이 둘이다.**"""
        발언 = {"의원인가": True, "사진키": "없음", "slug": None, "발언자명": "박지원"}
        assert meetings.resolve_의원코드(발언, self.사진색인, {}) is None


# 실측 55355(제429회 국회운영위원회회의록 제99호 · 2025-09-06). 정식 회의록과 다른 점은
# `p.tmp` 표지와 **발언 블록이 하나도 없다**는 것 둘뿐이다. 정상 회의록 7건을 표본으로
# 재 봤을 때 `p.tmp` 는 0건이었고 발언 블록은 3~1,356개였다.
임시회의록 = """
<html><body>
<script>const mnts_id = 55355; const class_id = 2;</script>
<div class="minutes_header"><div class="tit_wrap">
  <p class="turn">제429회 국회<br/>(정기회)</p>
  <div class="tit_in"><div class="tit"><h1>국회운영위원회회의록</h1></div>
    <p class="tmp">(임시회의록)</p></div>
  <p class="num">제99호</p></div></div>
<div class="minutes_body"></div>
</body></html>
"""

# 표지가 없는데 발언도 없는 문서 — 파서가 깨졌을 때의 모양이다.
발언없는정식 = """
<html><body>
<script>const mnts_id = 55355; const class_id = 2;</script>
<div class="minutes_header"><div class="tit"><h1>국회운영위원회회의록</h1></div></div>
<div class="minutes_body"></div>
</body></html>
"""


class Test임시회의록:
    """⚠️ **발언 0건에는 원인이 둘이고 처분이 반대다.**

    원천이 아직 텍스트 회의록을 안 만든 것(임시회의록)이면 다시 받아 봐야 같으므로
    `'없음'` 이고, 표지가 없는데 0건이면 **파서가 깨진 것**이라 `'막힘'` 으로 남겨
    사람이 고칠 때까지 감사가 계속 가리켜야 한다. 이 둘을 뭉뚱그려 `'재시도'` 로
    적으면 재시도 상한에 닿아 **A11 이 영구히 빨갛다** — 실측 55355 가 그 상태였다.
    """

    def test_임시회의록_표지를_읽는다(self):
        assert meetings.임시회의록인가(임시회의록)

    def test_정식_회의록에는_그_표지가_없다(self):
        assert not meetings.임시회의록인가(MINUTES)

    def test_표지가_없으면_발언이_0건이어도_임시회의록이_아니다(self):
        assert not meetings.임시회의록인가(발언없는정식)


class 가짜클라이언트:
    """`meetings.collect` 가 원천에 묻는 것 셋만 흉내낸다 — 열거 · 의원 사진 · 본문."""

    def __init__(self, 본문: dict, 열거=(), 의원행=(), 의원페이지=""):
        self.본문, self.열거, self.의원행, self.의원페이지 = 본문, 열거, 의원행, 의원페이지

    def all_pages(self, api, **kw):
        if api == meetings.열거_API:
            return list(self.열거) if kw.get("CONF_DATE") == 2025 else []
        if api == meetings.감사_API:
            return []
        if api == "ALLNAMEMBER":
            return list(self.의원행)
        raise AssertionError(f"묻지 않기로 한 API: {api}")

    def text(self, url, **kw):
        if "viewer/minutes" in url:
            return self.본문[int(kw["params"]["id"])]
        return self.의원페이지


def 열거한건(회의id: int, 위원회="국회운영위원회") -> list[dict]:
    return [
        {
            "CONFER_NUM": str(회의id), "CONF_ID": "N054999", "CLASS_NAME": "상임위원회",
            "COMM_NAME": 위원회, "CONF_DATE": "2025-09-06",
            "TITLE": f"제22대 제429회 제99차 {위원회} (2025년 09월 06일)",
            "SUB_NAME": "1. 간사 선임의 건",
        }
    ]


def 원장(conn, 대상종류: str) -> tuple:
    return conn.execute(
        "SELECT 실패종류, 상세 FROM 수집실패 WHERE 대상종류=?", (대상종류,)
    ).fetchone()


class Test본문실패의분류:
    """**재시도해도 안 되는 것을 재시도 큐에 두지 않는다.**

    `'재시도'` 는 다음 실행이 다시 받으면 풀릴 것에만 붙는다. 결정론적으로 같은 결과가
    나오는 것에 붙이면 시도횟수만 올라가 A11(재시도 상한)이 영구히 빨갛고, **그러면
    "빨강 = 사람이 봐야 한다"는 약속 자체가 죽는다** — 실측으로 6일간 그 상태였다.
    """

    def test_임시회의록은_없음으로_남기고_회의를_저장하지_않는다(self, conn):
        c = 가짜클라이언트({55355: 임시회의록}, 열거=열거한건(55355))
        수치 = meetings.collect(conn, c)
        assert 수치["실패"] == 1 and 수치["회의"] == 0
        assert conn.execute("SELECT COUNT(*) FROM 회의").fetchone()[0] == 0
        종류, 상세 = 원장(conn, "회의본문")
        assert 종류 == "없음", "다시 받아 봐야 같으므로 재시도 큐에 두지 않는다"
        assert "임시회의록" in 상세

    def test_표지가_없는데_발언이_0건이면_막힘이다(self, conn):
        """`'막힘'` 은 상한을 적용하지 않는다 — 사람이 파서를 고칠 때까지 A4 가 가리킨다."""
        c = 가짜클라이언트({55355: 발언없는정식}, 열거=열거한건(55355))
        meetings.collect(conn, c)
        종류, 상세 = 원장(conn, "회의본문")
        assert 종류 == "막힘"
        assert "0건" in 상세


class Test발언의원실패의분류:
    def test_의원코드를_못_푼_발언자는_재시도가_아니라_없음이다(self, conn):
        """실측: 원장의 5건을 지금 다시 받아 3단 경로를 그대로 태워도 미연결 수가
        **기록값과 정확히 같았다**(11=11 · 2=2 · 1=1 · 1=1 · 7=7). 원천이 그 발언자를
        식별할 정보를 주지 않는 것이라 재시도에 값이 없다."""
        c = 가짜클라이언트({57024: MINUTES}, 열거=열거한건(57024, "정무위원회"))
        수치 = meetings.collect(conn, c)
        assert 수치["회의"] == 1 and 수치["미연결"] == 2
        종류, 상세 = 원장(conn, "발언의원")
        assert 종류 == "없음"
        assert "2명" in 상세


# ── 중간에 죽으면 ───────────────────────────────────────────────────────────
#
# ⚠️ **`with conn:` 은 트랜잭션이 아니다.** `connect()` 가 `isolation_level=None`
#    (autocommit)로 열기 때문에 파이썬 sqlite3 의 컨텍스트 매니저는 열린 트랜잭션이
#    없는 상태에서 `commit()`/`rollback()` 을 부르고, **둘 다 아무 일도 안 한다.**
#    그래서 한 회의를 저장하는 블록 중간에서 터지면 앞부분이 그대로 커밋돼 있다.
#
#    그 반쪽 상태가 조용한 이유는 다음 실행이 `이미 = SELECT 회의id FROM 회의` 로
#    건너뛸 대상을 정하기 때문이다 — **부모가 있으니 다 됐다고 읽는다.** 발언 0행인
#    회의가 영구히 남고, 아무 에러도 나지 않는다(불변식 2 가 깨지는 자리).


새의원 = {
    # ⚠️ 꾸며 낸 상황이 아니다. `사진색인` 은 `ALLNAMEMBER` 가 준 코드를 **우리 `의원`
    #    표와 대조하지 않고** 그대로 쓰는데, `발언.의원코드` 에는 `의원` 을 향한 FK 가
    #    걸려 있고 `connect()` 가 `foreign_keys=ON` 을 건다. 원천이 먼저 올린 의원
    #    (보궐·승계로 새로 선서한 사람)이 우리 `의원` 표에 아직 없는 날, 그 사람이
    #    발언한 회의에서 정확히 이 일이 난다.
    "GTELT_ERACO": "제22대",
    "NAAS_PIC": "https://www.assembly.go.kr/static/portal/img/openassm/new/thumb/AAA111.PNG",
    "NAAS_CD": "아직없는의원",
}


class Test중간에죽어도:
    def test_발언_저장이_터지면_회의도_남지_않는다(self, conn):
        """되돌아가는 지점은 **회의 하나 통째로**여야 한다. 회의만 남고 발언이 없으면
        다음 실행이 그 회의를 '이미 했다'로 읽는다."""
        c = 가짜클라이언트({57024: MINUTES}, 열거=열거한건(57024, "정무위원회"),
                         의원행=[새의원])
        with pytest.raises(Exception):
            meetings.collect(conn, c)
        assert conn.execute("SELECT COUNT(*) FROM 회의").fetchone()[0] == 0, \
            "발언이 안 들어갔는데 회의만 남았다 — 다음 실행이 이걸 건너뛴다"

    def test_다음_실행이_그_회의를_다시_대상으로_잡는다(self, conn):
        """불변식 2 — 어디서 중단되든 다음 실행이 이어받아 끝까지 간다."""
        터지는것 = 가짜클라이언트({57024: MINUTES}, 열거=열거한건(57024, "정무위원회"),
                            의원행=[새의원])
        with pytest.raises(Exception):
            meetings.collect(conn, 터지는것)

        # 다음 날 의원 수집이 그 사람을 담았다 — 이제 원천 조건이 풀렸다.
        conn.execute("INSERT OR IGNORE INTO 의원 (의원코드, 이름) VALUES (?,?)",
                     ("아직없는의원", "새로온"))
        수치 = 가짜클라이언트({57024: MINUTES}, 열거=열거한건(57024, "정무위원회"),
                          의원행=[새의원])
        assert meetings.collect(conn, 수치)["회의"] == 1, \
            "중단된 회의를 다음 실행이 건너뛰었다"
        assert conn.execute(
            "SELECT COUNT(*) FROM 발언 WHERE 회의id=57024").fetchone()[0] > 0
