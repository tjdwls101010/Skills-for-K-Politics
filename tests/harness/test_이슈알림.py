"""`.github/scripts/notify.sh` — 침묵을 이슈 하나로 말한다.

⚠️ **알림이 매일 오면 그건 알림이 아니다.** 국회 수집은 6일 내리 빨간불이었는데
아무도 안 봤다 — 매일 같은 빨간 X 가 뜨는 것이 신호를 죽인 원인이었다.
그래서 이상이 이어지는 동안 **이슈는 하나이고 본문만 갱신**되고, 정상으로 돌아오면
닫힌다. 언제부터 이상해졌는지는 이슈의 생성 시각이 답한다.
"""

import pytest


def 호출들(기록, 조각):
    return [줄 for 줄 in 기록() if 조각 in 줄]


@pytest.fixture
def 본문(tmp_path):
    p = tmp_path / "본문.md"
    p.write_text("- `collect-congress.yml` — 마지막 성공 없음 🔴\n", encoding="utf-8")
    return str(p)


class Test이상일때:
    def test_열린_이슈가_없으면_새로_연다(self, gh, 본문):
        돌린다, 기록 = gh
        r = 돌린다("notify.sh", ["bad", 본문], {"issue list": ""}, REPO="o/r")
        assert r.returncode == 0, r.stderr
        assert 호출들(기록, "issue create"), "열린 이슈가 없으면 만들어야 한다"

    def test_이미_열려_있으면_본문만_갱신하고_또_만들지_않는다(self, gh, 본문):
        돌린다, 기록 = gh
        r = 돌린다("notify.sh", ["bad", 본문], {"issue list": "7"}, REPO="o/r")
        assert r.returncode == 0, r.stderr
        assert not 호출들(기록, "issue create"), "매일 새 이슈를 열면 그게 곧 노이즈다"
        수정 = 호출들(기록, "issue edit")
        assert 수정 and " 7 " in f" {수정[0]} "

    def test_본문에_실제_판정이_들어간다(self, gh, 본문):
        """이슈만 보고 무엇이 멈췄는지 알 수 있어야 한다 — 로그를 파러 가게 만들면 안 된다."""
        돌린다, 기록 = gh
        돌린다("notify.sh", ["bad", 본문], {"issue list": ""}, REPO="o/r")
        assert any("--body-file" in 줄 for 줄 in 호출들(기록, "issue create"))


class Test정상으로돌아왔을때:
    def test_열린_이슈를_닫는다(self, gh, 본문):
        돌린다, 기록 = gh
        r = 돌린다("notify.sh", ["ok", 본문], {"issue list": "7"}, REPO="o/r")
        assert r.returncode == 0, r.stderr
        assert 호출들(기록, "issue close")

    def test_열린_이슈가_없으면_아무것도_하지_않는다(self, gh, 본문):
        """정상일 때 조용한 것이 요점이다 — 매일 "정상입니다" 를 남기면 이슈가 쓰레기가 된다."""
        돌린다, 기록 = gh
        r = 돌린다("notify.sh", ["ok", 본문], {"issue list": ""}, REPO="o/r")
        assert r.returncode == 0
        assert not 호출들(기록, "issue create")
        assert not 호출들(기록, "issue close")


class Testgh가_실패하면_잡도_실패한다:
    """⚠️ **알림이 안 갔는데 워크플로가 초록이면 그 침묵은 영영 안 들린다.**

    이 스크립트가 하는 일은 「사람에게 말하기」 하나뿐이라, `gh` 가 실패했는데 0 으로
    끝나면 **워치독 잡은 성공으로 보이고 이슈는 없다.** 수집이 멈춘 것보다 나쁘다 —
    멈춘 것은 다음 실행이 이어받지만, 안 들린 알림은 다음이 없다.
    """

    @pytest.mark.parametrize(
        "상태, 열린이슈, 실패조각",
        [
            ("bad", "", "issue list"),
            ("bad", "", "issue create"),
            ("bad", "7", "issue edit"),
            ("ok", "7", "issue comment"),
            ("ok", "7", "issue close"),
        ],
    )
    def test_각_gh_호출의_실패가_비영_종료로_나온다(self, gh, 본문, 상태, 열린이슈, 실패조각):
        돌린다, _ = gh
        r = 돌린다(
            "notify.sh", [상태, 본문], {"issue list": 열린이슈},
            REPO="o/r", GH_FAIL=실패조각,
        )
        assert r.returncode != 0, f"`gh {실패조각}` 이 실패했는데 0 으로 끝났다"

    def test_라벨_만들기_실패는_알림을_막지_않는다(self, gh, 본문):
        """이미 있는 라벨을 또 만들면 실패하는데 그건 정상이다 — 여기서 죽으면
        **라벨이 있다는 이유로 이슈가 안 열린다.**"""
        돌린다, 기록 = gh
        r = 돌린다("notify.sh", ["bad", 본문], {"issue list": ""}, REPO="o/r", GH_FAIL="label create")
        assert r.returncode == 0, r.stderr
        assert 호출들(기록, "issue create")
