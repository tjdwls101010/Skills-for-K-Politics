"""목록 요청의 4xx 는 '없음'이 아니라 일시적 장애다.

실측 — 자동 수집이 `eflaw&nw=2` 목록에서 HTTP 404 를 받고 traceback 으로 죽었다.
같은 요청을 곧바로 다시 하면 정상이었다(6/6 성공). **일시적이었다.**

처음에는 이 판단이 목록에만 걸렸다 — "본문에는 없는 ID 가 실재하니 4xx 는 원천의
답이다"라고 봤기 때문이다. **그 절반이 틀렸다**(`test_본문404.py`): 원천은 부재를
HTTP 200 과 봉투로 말한다. 지금은 목록과 본문이 같은 판단을 쓴다.
"""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")
실행 = pytest.importorskip("법제처.수집기.실행")
원천 = pytest.importorskip("법제처.원천")

_목록응답 = {"LawSearch": {"totalCnt": "1", "law": [{"법령ID": "1", "법령일련번호": "9"}]}}


def _클라(응답들: list, **kw):
    """앞에서부터 하나씩 돌려주는 가짜 전송."""
    남은 = list(응답들)

    def 처리(request):
        코드, 몸 = 남은.pop(0) if len(남은) > 1 else 남은[0]
        return httpx.Response(코드, json=몸)

    c = 원천.Client(rate=1000, **kw)
    c.retries = 4
    c._c = httpx.Client(transport=httpx.MockTransport(처리))
    return c


def test_목록의_404_는_재시도한다(monkeypatch) -> None:
    monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
    with _클라([(404, {}), (200, _목록응답)]) as c:
        행, 총 = c.목록페이지("법령", 1)
    assert 총 == 1 and len(행) == 1
    assert c.요청수 == 2, "재시도하지 않았다"


def test_목록의_404_가_계속되면_없음이_아니라_오류다(monkeypatch) -> None:
    """⚠️ **`원천에없음` 으로 새어 나가면 안 된다.** 수집기가 그걸 '이 자료는 원천에
    없다'로 확정 처리해 **다시는 받지 않는다.**"""
    monkeypatch.setattr(원천.time, "sleep", lambda *_: None)
    with _클라([(404, {})]) as c:
        with pytest.raises(원천.API오류) as e:
            c.목록페이지("법령", 1)
        assert not isinstance(e.value, 원천.원천에없음)


def test_인증실패는_목록에서도_즉시_중단이다() -> None:
    """⚠️ 재시도로 감싸면서 인증 실패까지 5번 두드리면 안 된다."""
    with _클라([(200, {"result": "00", "msg": "인증 실패"})]) as c:
        with pytest.raises(원천.인증실패):
            c.목록페이지("법령", 1)
        assert c.요청수 == 1


# ── 종료코드 ────────────────────────────────────────────────




def test_원천_장애는_게이트_위반이_아니라_중단이다(monkeypatch, tmp_path) -> None:
    """⚠️ **실측으로 나온 오분류다.** 목록 404 가 traceback 으로 죽으면서 파이썬이 1 로
    나갔고, 워크플로가 그걸 `🔴 게이트 위반 — 사람이 봐야 한다` 로 요약했다.

    실제로는 원천이 흔들린 것이라 **사람이 할 일이 없고 다음 실행이 이어받는다** — 그게
    종료코드 2 다. 둘을 섞으면 진짜 게이트 위반이 왔을 때 사람이 무시하게 된다.
    """
    def 터진다(*a, **kw):
        raise 원천.API오류("http://…/lawSearch.do: HTTPStatusError: HTTP 404")

    monkeypatch.setattr(실행, "순서", [("법령", 터진다)])
    코드 = 실행._main(["--db", str(tmp_path / "t.db")])
    assert 코드 == 2, f"원천 장애가 {코드} 로 나갔다 (2=중단 이어야 한다)"


def test_인증_실패는_여전히_1_이다(monkeypatch, tmp_path) -> None:
    """인증은 다음 실행도 똑같이 실패한다 — 사람이 봐야 한다."""
    def 터진다(*a, **kw):
        raise 원천.인증실패("법령/목록: 00")

    monkeypatch.setattr(실행, "순서", [("법령", 터진다)])
    assert 실행._main(["--db", str(tmp_path / "t.db")]) == 1
