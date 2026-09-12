"""회의 열거가 도는 연도는 **달력에서 나와야 한다.**

⚠️ **`range(2024, 2027)` 은 2027-01-01 에 조용히 틀린다.** 그날부터 그 해 회의가 열거에서
통째로 빠지는데, 열거는 성공하고 수집도 성공하고 감사도 초록이다 — **"올해 것이 아직
안 올라왔나 보다"와 구별되지 않는다.** 회의록은 원래 수 주 뒤에 공개되므로 사람이 봐도
이상하지 않다. 상수를 늘리는 것으로는 못 고친다; 다음 상수도 언젠가 같은 날을 맞는다.

하한 2024 는 다르다 — 제22대 국회의 시작(2024-05-30)이라 달력이 아니라 **사실**이다.
"""

import pytest

from congress import meetings


class 물은연도만:
    """`collect` 가 열거에 무슨 연도를 물었는지만 남긴다."""

    def __init__(self):
        self.연도 = []

    def all_pages(self, api, **kw):
        if api == meetings.열거_API:
            self.연도.append(kw.get("CONF_DATE"))
        return []

    def text(self, url, **kw):
        raise AssertionError("본문을 물을 일이 없다")


def 도는연도(conn, monkeypatch, 그해: int) -> list[int]:
    monkeypatch.setattr(meetings, "올해", lambda: 그해)
    c = 물은연도만()
    meetings.collect(conn, c, 로그=lambda *_: None)
    return c.연도


class Test열거연도:
    def test_2027년에는_2027년_회의를_열거한다(self, conn, monkeypatch):
        """계획이 못박은 판정 — 가짜 시계를 2027 로 돌렸을 때 그 해가 열거에 든다."""
        assert 도는연도(conn, monkeypatch, 2027) == [2024, 2025, 2026, 2027]

    def test_하한은_제22대가_시작한_2024다(self, conn, monkeypatch):
        """22대 이전 회의는 이 DB 의 대상이 아니다(`DAE_NUM=22`). 하한까지 파생시키면
        원천에 없는 연도를 매년 하나씩 더 묻게 된다."""
        assert 도는연도(conn, monkeypatch, 2030)[0] == 2024

    def test_22대가_끝난_뒤로는_안_늘린다(self, conn, monkeypatch):
        """⚠️ **파생시킨다고 무한히 늘리면 반대쪽으로 틀린다.** 22대 임기는 2028-05-29 에
        끝나므로 2029년에는 22대 회의가 열릴 수 없다 — 매 실행 빈 요청이 하나씩 는다.
        상한도 하한과 같은 부류다: 달력이 아니라 **임기라는 사실**이다."""
        assert 도는연도(conn, monkeypatch, 2030) == [2024, 2025, 2026, 2027, 2028]

    def test_올해가_하한보다_앞서도_최소한_2024는_묻는다(self, conn, monkeypatch):
        """⚠️ 시계가 어긋난 러너에서 `range` 가 **빈손**이 되면 열거 0건이 성공으로
        읽힌다 — 원천이 조용히 빈손을 주는 것과 같은 모양이다."""
        assert 도는연도(conn, monkeypatch, 2020) == [2024]

    def test_올해는_KST_로_읽는다(self, monkeypatch):
        """⚠️ **UTC 로 읽으면 1월 1일 오전 9시까지 새해가 안 온다.** 러너는 UTC 다."""
        import datetime as dt

        class 가짜:
            @staticmethod
            def now(tz=None):
                # KST 로 2027-01-01 00:30, UTC 로는 아직 2026-12-31 15:30 이다.
                return dt.datetime(2026, 12, 31, 15, 30, tzinfo=dt.timezone.utc).astimezone(tz)

        monkeypatch.setattr(meetings, "datetime", 가짜)
        assert meetings.올해() == 2027
