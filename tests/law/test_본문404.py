"""본문 요청의 4xx 도 '없음'이 아니다 — **원천은 없다는 말을 4xx 로 하지 않는다.**

원래 판단은 "목록에는 '없는 레코드'가 없으니 4xx 는 일시 장애지만, 본문에는 없는 ID 가
실재하니 4xx 는 원천의 답이다"였다. 앞은 맞고 **뒤는 틀렸다.** 실측(2026-08-28)으로
네 본문 엔드포인트에 없는 ID 를 물으면 전부 이렇게 온다:

    HTTP 200  {"Law": "일치하는 판례가 없습니다.  판례명을 확인하여 주십시오."}

**부재는 200 과 봉투로 온다.** 그러니 본문의 4xx 는 원천의 답이 아니라 그 앞의 인프라다 —
목록의 4xx 와 정확히 같은 것이고, 판단도 같아야 한다.

⚠️ **이건 이미 자료를 잃었다.** 같은 실측에서 `수집실패` 의 '없음' 507건 중 **506건이
HTTP 4xx 에서 왔고 봉투에서 온 것은 1건뿐**이었다. 전부 2026-08-14 한 실행에서 찍혔다.
그중 다섯을 무작위로 골라 지금 원천에 물으니 **5/5 가 정상으로 온다** — 원천에 멀쩡히
있는 506건이 "원천에 없다"로 확정돼 다시는 요청되지 않고 있었다.

'없음'은 재시도 대상에서 영구히 빠지는 표지다(`없음인것`). 그 표지를 붙일 자격은
**원천이 그렇게 말했을 때**뿐이다.
"""

from __future__ import annotations

import sqlite3

import pytest

httpx = pytest.importorskip("httpx")
루프 = pytest.importorskip("law.collectors.loop")
스키마 = pytest.importorskip("law.schema")
원천 = pytest.importorskip("law.source")
저장 = pytest.importorskip("law.store")


_본문응답 = {"PrecService": {"판례정보일련번호": "700", "사건번호": "2020다1"}}
_없다는봉투 = {"Law": "일치하는 판례가 없습니다.  판례명을 확인하여 주십시오."}


def _클라(응답들, **kw):
    """앞에서부터 하나씩 돌려주는 가짜 전송. 마지막 것은 계속 되풀이된다."""
    남은 = list(응답들)

    def 처리(request):
        코드, 몸 = 남은.pop(0) if len(남은) > 1 else 남은[0]
        return httpx.Response(코드, json=몸)

    c = 원천.Client(rate=1000, **kw)
    c.retries = 4
    c.워커 = 1
    c._c = httpx.Client(transport=httpx.MockTransport(처리))
    return c


class Test본문의4xx:
    def test_한_번_흔들린_4xx_는_재시도로_지나간다(self, monkeypatch) -> None:
        """목록에서 이미 실측된 모양이다 — 404 를 받고 곧바로 다시 물으면 정상이었다."""
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        with _클라([(404, {}), (200, _본문응답)]) as c:
            몸통 = c.본문하나("판례", "700")
        assert 몸통["판례정보일련번호"] == "700"
        assert c.요청수 == 2, "본문 4xx 를 재시도하지 않았다"

    def test_4xx_가_계속돼도_없음이_아니다(self, monkeypatch) -> None:
        """⚠️ **`원천에없음` 으로 새어 나가면 그 자료는 영영 안 받아진다.** 재시도를 다
        써도 4xx 라면 그건 원천이 오래 흔들린 것이지, 원천이 '없다'고 답한 것이 아니다 —
        원천은 없을 때 200 과 봉투로 답한다."""
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        with _클라([(404, {})]) as c:
            with pytest.raises(원천.API오류) as e:
                c.본문하나("판례", "700")
            assert not isinstance(e.value, 원천.원천에없음), (
                "본문 4xx 가 '없음'으로 확정됐다 — 506건을 잃은 그 경로다")

    def test_원천이_없다고_말하는_방식은_200_봉투다(self) -> None:
        """여기는 재시도하지 않는다. 원천의 답이므로 다시 물어도 같다."""
        with _클라([(200, _없다는봉투)]) as c:
            with pytest.raises(원천.원천에없음):
                c.본문하나("판례", "없는ID")
            assert c.요청수 == 1, "원천의 답을 재시도했다 — 요청만 5배 쓴다"

    def test_인증실패는_본문에서도_즉시_중단이다(self) -> None:
        with _클라([(200, {"result": "00", "msg": "인증 실패"})]) as c:
            with pytest.raises(원천.인증실패):
                c.본문하나("판례", "700")
            assert c.요청수 == 1


class Test다음_실행이_이어받는가:
    """계획의 완료 판정 — *일시 장애를 흉내낸 가짜 원천에서 그 ID 가 **다음 실행 대상에
    남아 있는지**로 판정한다.*"""

    @staticmethod
    def _DB():
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.executescript(스키마.SCHEMA)
        return conn

    @staticmethod
    def _파싱(몸통, 목록행):
        return {"판례ID": 몸통["판례정보일련번호"], "사건번호": "2020다1",
                "수집일시": "2026-08-28 00:00:00"}, None

    def _돈다(self, conn, c):
        return 루프.수집루프(conn, c, "판례", {"700": {}}, self._파싱,
                          로그=lambda *a: None, 검증된목록=True)

    def test_장애로_못_받은_본문을_다음_실행이_받는다(self, monkeypatch) -> None:
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        conn = self._DB()

        with _클라([(404, {})]) as c:          # 원천이 통째로 흔들린 날
            수치 = self._돈다(conn, c)
        assert 수치["본문"] == 0 and 수치["없음"] == 0, (
            f"4xx 를 '없음'으로 확정했다: {수치}")

        with _클라([(200, _본문응답)]) as c:    # 다음 날
            수치 = self._돈다(conn, c)
        assert 수치["본문"] == 1, "다음 실행이 그 ID 를 다시 묻지 않았다"
        assert conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0] == 1

    def test_원천이_없다고_한_것은_다음_실행이_다시_묻지_않는다(self) -> None:
        """⚠️ 반대쪽도 지켜야 한다. 진짜 부재까지 매일 다시 물으면 수만 건을 헛되이
        두드린다 — 그게 '없음' 표지가 있는 이유다."""
        conn = self._DB()
        with _클라([(200, _없다는봉투)]) as c:
            수치 = self._돈다(conn, c)
            assert 수치["없음"] == 1
        with _클라([(200, _본문응답)]) as c:
            self._돈다(conn, c)
            assert c.요청수 == 0, "원천이 없다고 한 것을 다시 물었다"


class Test옛_표지를_되돌린다:
    """⚠️ **앞을 막는 것만으로는 이미 잃은 506건이 안 돌아온다.** '없음'은 재시도 대상에서
    영구히 빠지는 표지라, 새 코드가 그 표지를 안 붙이게 되어도 **이미 붙은 것은 그대로
    남는다.** 우리 원장이 무엇에서 왔는지를 적어 두었으니(`메시지`), 붙일 자격이 없었던
    표지는 우리가 걷는다.

    ⚠️ **걷는 자리는 "실제로 다시 묻는다"에 결박된다.** 적재 등식이 본문 키와 '없음'의
    합집합을 보므로, 이번에 안 물을 것의 표지를 걷으면 그 자료는 본문도 '없음'도 없는
    상태가 되고 **이번 실행이 고칠 수 없는 방향으로** 등식이 깨진다.
    """

    @staticmethod
    def _DB():
        conn = sqlite3.connect(":memory:", isolation_level=None)
        conn.executescript(스키마.SCHEMA)
        return conn

    def test_4xx_에서_온_것만_고른다(self) -> None:
        conn = self._DB()
        저장.record_failure(conn, "판례", "A", "없음",
                          "http://www.law.go.kr/DRF/lawService.do: HTTP 404")
        저장.record_failure(conn, "판례", "B", "없음",
                          "판례/본문: 일치하는 판례가 없습니다.  판례명을 확인하여 주십시오.")
        저장.record_failure(conn, "판례", "C", "재시도", "ReadTimeout")

        assert 저장.잘못붙은없음(conn, "판례") == {"A"}, "원천이 말한 부재까지 고르면 안 된다"

    def test_다시_물어_받으면_표지가_걷힌다(self, monkeypatch) -> None:
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        conn = self._DB()
        저장.record_failure(conn, "판례", "700", "없음", "…: HTTP 404")

        with _클라([(200, _본문응답)]) as c:
            수치 = 루프.수집루프(conn, c, "판례", {"700": {}},
                             Test다음_실행이_이어받는가._파싱, 로그=lambda *a: None)

        assert 수치["본문"] == 1
        assert 저장.없음인것(conn, "판례") == set()
        assert conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0] == 1

    def test_원천이_없다고_한_표지는_안_걷는다(self) -> None:
        """⚠️ 반대쪽도 지켜야 한다 — 진짜 부재까지 매일 다시 물으면 수만 건을 헛되이
        두드린다."""
        conn = self._DB()
        저장.record_failure(conn, "판례", "700", "없음", "판례/본문: 일치하는 판례가 없습니다.")

        with _클라([(200, _본문응답)]) as c:
            루프.수집루프(conn, c, "판례", {"700": {}},
                        Test다음_실행이_이어받는가._파싱, 로그=lambda *a: None)
            assert c.요청수 == 0, "원천이 없다고 한 것을 다시 물었다"
        assert 저장.없음인것(conn, "판례") == {"700"}

    def test_표본_밖의_표지는_안_걷는다(self, monkeypatch) -> None:
        """⚠️ **`--표본 N` 이 이 함정의 정확한 모양이다.** 표지는 전부 걷었는데 실제로
        묻는 것은 앞의 N 건뿐이면, 나머지는 본문도 '없음'도 없는 상태로 남아 A1 이
        **이번 실행으로는 고칠 수 없게** 깨진다. 워크플로가 `inputs.sample` 을 실제로
        넘긴다."""
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        conn = self._DB()
        목록 = {}
        for i in (700, 701):
            저장.record_failure(conn, "판례", str(i), "없음", "…: HTTP 404")
            목록[str(i)] = {}

        with _클라([(200, _본문응답)]) as c:
            루프.수집루프(conn, c, "판례", 목록, Test다음_실행이_이어받는가._파싱,
                        로그=lambda *a: None, 표본=1)

        남은표지 = 저장.없음인것(conn, "판례")
        본문 = conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0]
        assert 본문 + len(남은표지) == len(목록), (
            f"적재 등식이 깨졌다: 본문 {본문} + 없음 {len(남은표지)} != 목록 {len(목록)}")

    def test_되돌릴것이_표본_맨_앞에_선다(self, monkeypatch) -> None:
        """⚠️ 뒤에 두면 `--표본` 실행에서 **몇 번을 돌려도 영영 안 물어진다** — 목록 순서가
        같으니 매번 같은 앞자리가 표본을 채운다. 되물을 것을 앞에 둔 것과 같은 이유다."""
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        conn = self._DB()
        목록 = {str(i): {} for i in range(700, 710)}
        저장.record_failure(conn, "판례", "709", "없음", "…: HTTP 404")

        with _클라([(200, _본문응답)]) as c:
            루프.수집루프(conn, c, "판례", 목록, Test다음_실행이_이어받는가._파싱,
                        로그=lambda *a: None, 표본=1)
        assert c.요청수 == 1 and 저장.없음인것(conn, "판례") == set(), (
            "표본 한 자리가 709 가 아닌 앞자리로 갔다 — 목록 순서가 매번 같으니 "
            "몇 번을 돌려도 709 는 영영 안 물어진다")


class Test장애가_남기는_것:
    """⚠️ **여기는 고른 것이지 사고가 아니다.** 끝내 못 받은 본문은 `'없음'`(영구 확정)이
    아니라 `'재시도'` 로 남고, 그러면 **잔존 게이트가 그 실행에서 빨갛다.** 타임아웃·5xx 가
    이미 그렇게 동작하며, 다음 실행이 받으면 풀린다 — 고칠 수 없는 빨간불이 아니다.

    반대쪽을 골랐다면(4xx 를 계속 '없음'으로 두면) 게이트는 초록인데 자료는 영영 안 온다.
    **초록인 채로 잃는 것보다 빨간 채로 남는 쪽**이다.
    """

    def test_끝내_못_받으면_재시도로_남는다(self, monkeypatch) -> None:
        monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
        conn = Test옛_표지를_되돌린다._DB()

        with _클라([(404, {})]) as c:
            수치 = 루프.수집루프(conn, c, "판례", {"700": {}},
                             Test다음_실행이_이어받는가._파싱, 로그=lambda *a: None)

        assert 수치 == 수치 | {"본문": 0, "없음": 0, "실패": 1}
        assert conn.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 자료ID='700'").fetchone()[0] == "재시도"
        assert 저장.없음인것(conn, "판례") == set(), "'없음'으로 확정하면 영영 안 받는다"
