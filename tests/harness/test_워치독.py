"""`.github/scripts/watchdog.sh` — 수집이 멈췄는지 맥 밖에서 본다.

⚠️ **이 감시자는 실제로 도입 이후 한 번도 감시를 못 했다.** 인라인 셸에 `문제=0` 이라고
써 있었는데 bash 는 비ASCII 변수명을 거부한다 — `command not found` 로 exit 127 이
되어 매일 빨간불만 냈다. 그 상태로 국회 수집이 6일간 죽어 있는 것을 아무도 몰랐다.
**감시자가 조용히 죽는 것이 이 레포에서 실제로 일어난 일이라 여기에 회귀 검사를 둔다.**
"""

from datetime import datetime, timedelta, timezone


def 시각(시간전: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=시간전)).strftime("%Y-%m-%dT%H:%M:%SZ")


def 성공질의(wf: str) -> str:
    return f"workflows/{wf}/runs?status=success"


def 연속질의(wf: str) -> str:
    return f"workflows/{wf}/runs?status=completed&per_page=20"


class Test판정:
    def test_전부_최신이면_초록으로_끝난다(self, gh):
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml", "collect-law.yml"],
                  {성공질의("collect-congress.yml"): 시각(2), 성공질의("collect-law.yml"): 시각(3)},
                  REPO="o/r")
        assert r.returncode == 0, f"stderr={r.stderr}"
        assert r.stdout.count("🟢") == 2 and "🔴" not in r.stdout

    def test_비ASCII_식별자로_죽지_않는다(self, gh):
        """exit 127 · `command not found` 가 다시 들어오면 여기서 잡힌다."""
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml"], {성공질의("collect-congress.yml"): 시각(1)}, REPO="o/r")
        assert r.returncode == 0
        assert "command not found" not in r.stderr

    def test_기준을_넘으면_빨갛고_몇_시간인지_말한다(self, gh):
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml"], {성공질의("collect-congress.yml"): 시각(50)},
                  REPO="o/r", STALE_HOURS="30")
        assert r.returncode == 1
        assert "🔴" in r.stdout and "50시간 전" in r.stdout

    def test_성공_이력이_아예_없으면_빨갛다(self, gh):
        """빈손을 초록으로 넘기면 **한 번도 성공한 적 없는 워크플로가 정상으로 보인다.**"""
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml"], {성공질의("collect-congress.yml"): ""}, REPO="o/r")
        assert r.returncode == 1
        assert "없다" in r.stdout

    def test_gh_가_실패해도_조용히_초록으로_끝나지_않는다(self, gh):
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml"], {}, REPO="o/r", GH_MISS_EXIT="1")
        assert r.returncode == 1

    def test_한쪽이_멀쩡해도_다른_쪽의_빨강을_덮지_않는다(self, gh):
        """워크플로마다 따로 본다 — 하나로 묶으면 감시자가 감시 대상을 덮어 준다."""
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["collect-congress.yml", "collect-law.yml"],
                  {성공질의("collect-congress.yml"): 시각(1), 성공질의("collect-law.yml"): 시각(99)},
                  REPO="o/r", STALE_HOURS="30")
        assert r.returncode == 1
        assert "🟢" in r.stdout and "🔴" in r.stdout


class Test자기감시:
    """⚠️ **감시자 자신도 안 뜬 적이 있다** — 2026-08-16·17 이틀은 예약 실행이 통째로
    없었다. 그런데 자기 자신을 `success` 로 보면, 수집이 멈춰서 자기가 빨개진 것 때문에
    다음 날 **자기도 늙은 것으로 세어져 문제 하나가 둘로 불어난다.**
    """

    def test_자기_자신은_성공이_아니라_실행을_본다(self, gh):
        돌린다, 기록 = gh
        r = 돌린다("watchdog.sh", ["watchdog.yml:run"],
                  {"workflows/watchdog.yml/runs?status=completed": 시각(20)}, REPO="o/r")
        assert r.returncode == 0
        assert "마지막 실행" in r.stdout
        assert not any("status=success" in 줄 for 줄 in 기록()), "자기 자신에 성공 필터를 걸면 안 된다"

    def test_지금_도는_자기_실행을_세지_않는다(self, gh):
        """⚠️ **`?per_page=1` 만 쓰면 지금 도는 자기 자신이 잡혀 언제나 초록이다.**

        실측으로 처음 배선했을 때 정확히 그렇게 됐다 — 로컬에서 94시간 전이라고
        말하던 것이, 워크플로 안에서 돌자 `마지막 실행 (0시간 전) 🟢` 으로 나왔다.
        감시가 통째로 헛돈 것이고 로그를 안 읽었으면 못 봤다.
        `status=completed` 는 진행 중인 실행을 빼 준다.
        """
        돌린다, 기록 = gh
        돌린다("watchdog.sh", ["watchdog.yml:run"],
              {"workflows/watchdog.yml/runs?status=completed": 시각(20)}, REPO="o/r")
        물은것 = [줄 for 줄 in 기록() if "watchdog.yml" in 줄]
        assert 물은것 and all("status=completed" in 줄 for 줄 in 물은것)

    def test_며칠째_안_뜨면_빨갛다(self, gh):
        돌린다, _ = gh
        r = 돌린다("watchdog.sh", ["watchdog.yml:run"],
                  {"workflows/watchdog.yml/runs?status=completed": 시각(72)},
                  REPO="o/r", STALE_HOURS="30")
        assert r.returncode == 1 and "72시간 전" in r.stdout


class Test연속_실패_횟수:
    """⚠️ **"오래됐다"는 하루짜리 흔들림과 엿새째 죽어 있는 것을 같은 얼굴로 만든다.**

    이슈 본문은 갱신되고 제목은 그대로라, 사람이 보는 것은 "몇 시간 전"뿐이다. 그런데
    그 숫자는 기준을 막 넘긴 31시간이든 엿새째든 비슷하게 읽힌다 — 실제로 국회 수집이
    6일 내리 빨갰는데 아무도 안 봤다. **몇 번을 내리 실패했는지가 그 둘을 가른다.**
    """

    def test_빨간_줄에_마지막_성공_이후_실패_횟수가_붙는다(self, gh):
        돌린다, _ = gh
        r = 돌린다(
            "watchdog.sh", ["collect-congress.yml"],
            {성공질의("collect-congress.yml"): 시각(50), 연속질의("collect-congress.yml"): "6"},
            REPO="o/r", STALE_HOURS="30",
        )
        assert r.returncode == 1
        assert "6회" in r.stdout, f"연속 실패 횟수가 없다: {r.stdout}"

    def test_초록_줄은_횟수로_어지럽히지_않는다(self, gh):
        """정상인 줄에 `0회 연속 실패` 를 붙이면 눈이 그 열을 통째로 건너뛰게 된다."""
        돌린다, _ = gh
        r = 돌린다(
            "watchdog.sh", ["collect-congress.yml"],
            {성공질의("collect-congress.yml"): 시각(2), 연속질의("collect-congress.yml"): "0"},
            REPO="o/r",
        )
        assert r.returncode == 0
        assert "회" not in r.stdout.replace("시간", ""), r.stdout

    def test_횟수를_못_읽어도_빨간불_자체는_살아_있다(self, gh):
        """⚠️ 부가 정보를 못 얻은 것이 **판정을 못 하는 것이 되면 안 된다.**"""
        돌린다, _ = gh
        r = 돌린다(
            "watchdog.sh", ["collect-congress.yml"],
            {성공질의("collect-congress.yml"): 시각(50)},
            REPO="o/r", STALE_HOURS="30", GH_FAIL="per_page=20",
        )
        assert r.returncode == 1
        assert "🔴" in r.stdout
