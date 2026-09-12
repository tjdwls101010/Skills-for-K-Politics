"""수집 워크플로 YAML — **여기서 조용히 틀리면 하루 1회 자동 수집이 통째로 헛돈다.**

셋을 잠근다. ① 러너가 도중에 잠들지 않는가 ② 로그가 실시간으로 흐르는가
③ 판정(종료코드)이 파이프와 스텝을 건너 그대로 전파되는가.
"""

from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
워크플로 = 레포 / ".github" / "workflows"
수집들 = {"국회": 워크플로 / "collect-congress.yml", "법령": 워크플로 / "collect-law.yml"}
운영루트 = Path("/Users/seongjin/Coding/Skills for K-Politics")


def 본문(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def 명령들(글: str) -> list[str]:
    """셸 명령을 한 줄씩. **백슬래시 연결을 먼저 잇는다** — 안 그러면 여러 줄로 쪼갠
    명령이 앞머리(`caffeinate ... \\`)와 몸통(`uv run ...`)으로 갈려 검사가 헛돈다.

    ⚠️ **주석은 명령이 아니다.** `uv run` 을 *말하는* 주석("uv run 으로 부르지 마라")까지
    세면, 배선을 설명하는 문장 하나가 배선 검사를 빨갛게 만든다 — 실측으로 그랬다.
    """
    이어붙인 = 글.replace("\\\n", " ")
    return [줄.strip() for 줄 in 이어붙인.splitlines()
            if not 줄.strip().startswith("#")]


@pytest.mark.parametrize(
    "이름,환경변수,DB",
    [("국회", "CONGRESS_DB", ".claude/skills/k-politics/DBs/CONGRESS.db"),
     ("법령", "LAW_DB", ".claude/skills/k-politics/DBs/LAW.db")],
)
def test_수집이_운영_DB를_겨눈다(이름, 환경변수, DB):
    """레포 디렉터리를 옮긴 날 옛 절대경로를 계속 겨누면 수집은 DB 점검에서 멈춘다."""
    예상 = f"{환경변수}: {운영루트 / DB}"
    assert 예상 in 본문(수집들[이름])


@pytest.mark.parametrize("이름", sorted(수집들))
class Test러너가자다죽지않는다:
    """⚠️ **실측: 세 번의 예약 실행이 전부 "runner lost communication" 으로 죽었다**
    (8/14 18분 · 8/15 10분 · 8/19 11분). `pmset -g log` 에 그 순간이 남아 있다 —
    `04:34:59 PID 73724(caffeinate) ClientDied … 00:17:02` 직후 `04:35:57 Entering
    Sleep state`. 잡이 뜬 뒤에 맥이 잠든 것이라, **자고 있어서 안 도는 것이 아니다.**
    `caffeinate` 는 명령이 끝나면 자동으로 풀리므로 시스템 설정을 바꾸지 않는다.
    """

    def test_네트워크를_타는_스텝은_caffeinate_아래에서_돈다(self, 이름):
        글 = 본문(수집들[이름])
        긴스텝 = [줄 for 줄 in 명령들(글) if "uv run" in 줄]
        assert 긴스텝, "네트워크를 타는 스텝이 하나도 없다면 이 검사의 전제가 깨진 것이다"
        안걸린것 = [줄 for 줄 in 긴스텝 if "caffeinate" not in 줄]
        assert not 안걸린것, f"잠을 안 막는 스텝이 남아 있다: {안걸린것}"


@pytest.mark.parametrize("이름", sorted(수집들))
class Test로그가실시간으로흐른다:
    """⚠️ **러너가 죽으면 `upload-artifact` 가 403 으로 실패해 파일 로그가 통째로
    사라진다**(2026-08-19 실측). 그때 남는 것은 stdout 으로 흘린 것뿐이다.
    """

    def test_파이썬_출력을_버퍼에_담아_두지_않는다(self, 이름):
        assert "PYTHONUNBUFFERED=1" in 본문(수집들[이름])

    def test_파일로만_받지_말고_화면으로도_흘린다(self, 이름):
        글 = 본문(수집들[이름])
        assert "| tee collect.txt" in 글
        assert "> collect.txt" not in 글, "리다이렉트만 하면 끝날 때까지 화면이 빈다"


@pytest.mark.parametrize("이름", sorted(수집들))
class Test판정이그대로전파된다:
    def test_파이프_너머로_종료코드를_잃지_않는다(self, 이름):
        """`cmd | tee` 는 셸이 마지막 명령(tee)의 0 을 돌려준다 — 게이트 위반의 1 이 덮인다."""
        assert "set -o pipefail" in 본문(수집들[이름])

    def test_원천_진단이_빨가면_수집하지_않는다(self, 이름):
        """원천의 봉투나 필드가 바뀐 채로 수집하면 새 형식을 그대로 적재하고,
        감사가 그걸 못 잡으면 **작업 전체가 초록으로 끝난다.**"""
        assert "if: steps.verify.outputs.code == '0'" in 본문(수집들[이름])

    def test_원천_진단_실패도_잡을_빨갛게_만든다(self, 이름):
        """수집 종료코드만 보면, 진단이 빨개서 수집을 건너뛴 실행이 **초록으로 끝난다** —
        원천이 바뀐 날 아무도 모르게 된다."""
        판정 = 본문(수집들[이름]).split("판정 전파", 1)[1]
        assert "steps.verify.outputs.code" in 판정


class Test판정이_스텝을_건너_살아남는다:
    """⚠️ **앞 스텝이 죽으면 뒤 스텝은 안 돈다 — 그게 판정을 삼킨다.**

    `verify` 가 빨간 날 `collect.txt` 는 아예 만들어지지 않는데, 요약이 그 파일을 읽으면
    요약 스텝이 실패한다. `always()` 가 없는 「판정 전파」는 그래서 안 돌고, **원천이
    바뀐 날의 빨간불이 통째로 사라진다.** 잡은 요약 실패로 빨갛게 끝나므로 얼핏 괜찮아
    보이지만, 그 빨강은 "원천이 바뀌었다"가 아니라 "요약이 파일을 못 찾았다"이다.
    """

    def test_판정_전파는_앞_스텝이_죽어도_돈다(self):
        글 = 본문(수집들["국회"])
        뒤 = 글.split("- name: 판정 전파", 1)
        assert len(뒤) == 2, "판정 전파 스텝이 없다"
        머리 = 뒤[1].split("run: |", 1)[0]
        assert "if: always()" in 머리, "판정 전파가 앞 스텝의 실패에 딸려 사라진다"

    def test_verify_가_빨간_날_요약이_읽을_것이_있다(self):
        """`collect.txt` 는 그날 만들어지지 않는다 — 그것만 읽으면 요약이 실패한다."""
        요약 = 본문(수집들["국회"]).split("- name: 요약", 1)[1].split("- uses:", 1)[0]
        assert "--verify-로그 verify.txt" in 요약, "verify 가 빨간 날 요약이 아무것도 못 읽는다"
        # ⚠️ **요약이 0 아닌 것으로 끝나면 진짜 판정 옆에 두 번째 빨간불이 생긴다.**
        #    그 계약은 `tests/harness/test_요약렌더러.py` 가 실제로 돌려 보며 잰다.
        assert ".github/scripts/요약.py" in 요약

    @pytest.mark.parametrize("이름", sorted(수집들))
    def test_DB_점검_실패가_최종_판정까지_살아남는다(self, 이름):
        """DB가 없다는 원인을 verify 실패나 요약 실패로 바꿔 보고하면 복구할 자리가 흐려진다."""
        글 = 본문(수집들[이름])
        점검 = 글.split("- name: 원본이 우리가 아는 그 파일인가", 1)[1].split("- name:", 1)[0]
        판정 = 글.split("- name: 판정 전파", 1)[1]
        assert "id: preflight" in 점검
        assert "if: always()" in 판정.split("run: |", 1)[0]
        assert "steps.preflight.outcome" in 판정
        assert "DB 경로 점검 실패" in 판정

    @pytest.mark.parametrize("이름", sorted(수집들))
    def test_DB_점검_실패가_요약과_아티팩트에_남는다(self, 이름):
        """뒤 스텝의 파일이 없는 날에도 DB 점검 로그가 실행 요약과 아티팩트에 남아야 한다."""
        글 = 본문(수집들[이름])
        점검 = 글.split("- name: 원본이 우리가 아는 그 파일인가", 1)[1].split("- name:", 1)[0]
        요약 = 글.split("- name: 요약", 1)[1].split("- uses:", 1)[0]
        아티팩트 = 글.split("actions/upload-artifact", 1)[1].split("- name: 판정 전파", 1)[0]
        assert "set -o pipefail" in 점검 and "| tee preflight.txt" in 점검
        assert "steps.preflight.outcome" in 요약
        # 읽는 파일을 워크플로가 직접 말한다. 렌더러의 기본값에 맡기면 **yml 만 읽어서는
        # 그날 무엇이 요약에 남는지 알 수 없다** — 배선이 코드 안으로 숨는다.
        assert "--preflight-로그 preflight.txt" in 요약
        assert "preflight.txt" in 아티팩트


class Test락_물러남은_실패가_아니다:
    """수집기가 3 을 내면(락을 못 잡아 물러남) 잡은 초록이되 경고 주석으로 구별된다.
    ⚠️ 한쪽만 그러면 같은 사건이 국회에서는 경고, 법령에서는 빨강이 된다."""

    @pytest.mark.parametrize("이름", sorted(수집들))
    def test_판정_전파가_3_을_경고_초록으로_읽는다(self, 이름):
        판정 = 본문(수집들[이름]).split("- name: 판정 전파", 1)[1]
        assert '[ "$c" = "3" ]' in 판정, "3 을 exit 3 으로 흘리면 잡이 빨개진다"
        assert "::warning::" in 판정


class Test워치독워크플로:
    """감시자는 감시 대상 **밖**에 있어야 하고, 자기가 빨개지는 것으로 말하면 안 된다."""

    파일 = 워크플로 / "watchdog.yml"

    def test_감시_대상인_맥에서_돌지_않는다(self):
        """러너가 죽는 순간 워치독도 같이 죽으면 아무 소용이 없다."""
        # 주석에도 `self-hosted` 라는 낱말이 나오므로 `runs-on:` 줄만 본다.
        도는곳 = [줄.strip() for 줄 in 본문(self.파일).splitlines() if 줄.strip().startswith("runs-on:")]
        assert 도는곳 == ["runs-on: ubuntu-latest"], 도는곳

    def test_이슈를_열고_닫을_권한을_갖는다(self):
        """권한을 안 주면 `gh issue create` 가 403 으로 죽는다 — 알림이 통째로 사라진다."""
        글 = 본문(self.파일)
        assert "issues: write" in 글

    def test_인라인_셸이_아니라_스크립트를_부른다(self):
        """인라인으로 되돌아가면 회귀 검사를 붙일 자리가 사라진다 — 이 레포에서 감시자가
        도입 이후 한 번도 안 돈 것을 아무도 몰랐던 이유가 그것이다."""
        글 = 본문(self.파일)
        assert ".github/scripts/watchdog.sh" in 글
        assert ".github/scripts/notify.sh" in 글

    def test_자기_자신도_감시_대상에_넣는다(self):
        """2026-08-16·17 이틀은 예약 실행이 통째로 없었다 — 감시자도 안 뜬다."""
        assert "watchdog.yml:run" in 본문(self.파일)

    def test_두_수집과_백업을_감시한다(self):
        글 = 본문(self.파일)
        for 파일 in ("collect-congress.yml", "collect-law.yml", "backup-db.yml"):
            assert f"{파일}:success" in 글
        assert "test.yml:success" not in 글

    def test_기준_시간이_하루보다_여유가_있다(self):
        """⚠️ 예약이 하루 1회인데 기준이 30시간이면 여유가 여섯 시간뿐이다. GitHub 의
        예약은 정각 부하로 흔히 밀리므로, 정상 지연 한 번이 곧 빨간불이 된다 —
        **늑대를 부르는 감시자는 다음 진짜 신호도 같이 잃는다.**"""
        시간 = [줄 for 줄 in 본문(self.파일).splitlines() if "STALE_HOURS:" in 줄]
        assert 시간 and "'36'" in 시간[0], 시간

    def test_이상을_찾아도_잡을_빨갛게_만들지_않는다(self):
        """⚠️ **신호는 이슈다.** 매일 빨간 X 를 하나 더 만들면 그게 곧 다음 번 무시의
        원인이 된다 — 국회 수집이 6일 내리 빨갛다가 아무도 안 본 것이 그 결과다.
        잡이 빨간 것은 **점검 자체가 못 돈** 경우로 남겨 둔다."""
        글 = 본문(self.파일)
        assert "exit ${{ steps.watch.outputs.code }}" not in 글


class Test예약시각:
    """⚠️ **정각은 GitHub 전체가 몰리는 시각이다.** 예약 실행은 부하로 밀리거나 통째로
    흘려지고, 실측 2026-08-16·17 이틀은 국회·법령 예약이 **실행 기록조차 없었다.**
    분을 흩어 두는 것은 공짜이고, 밀린 실행과 안 뜬 실행은 사후에 구별되지 않는다.
    """

    def test_어떤_예약도_정각에_걸려_있지_않다(self):
        걸린것 = []
        for 파일 in sorted(워크플로.glob("*.yml")):
            for 줄 in 본문(파일).splitlines():
                조각 = 줄.strip()
                if 조각.startswith("- cron:") and 조각.split('"')[1].split()[0] == "0":
                    걸린것.append(f"{파일.name}: {조각}")
        assert not 걸린것, f"정각 예약이 남아 있다: {걸린것}"


class TestPR체크:
    """⚠️ **PR 에서 도는 테스트가 `tests/kosis/` 뿐이었다.**

    그 사이 `tests/congress/` 는 경로가 어긋나 `pytest tests/` 자체가 collection 에러로
    통째로 중단되는 상태였는데 CI 가 그걸 못 잡았다. 안전망이 꺼져 있는 것을 안전망이
    알려 주지 못하는 구조였다.
    """

    def test_PR_에서_세_코퍼스_전부를_돌린다(self):
        점검 = 워크플로 / "test.yml"
        오프라인 = 본문(점검).split("  source-contract:", 1)[0]
        assert "uv run --group test pytest -q" in 오프라인, \
            "한 코퍼스만 돌리면 나머지 둘의 안전망이 CI 에서 꺼진 것이다"

    def test_congress_law_테스트가_요구하는_의존성을_준다(self):
        """`selectolax` 가 없으면 congress 테스트가 import 단계에서 통째로 죽는다."""
        오프라인 = 본문(워크플로 / "test.yml").split("  source-contract:", 1)[0]
        assert "uv run --group test pytest -q" in 오프라인
        의존성 = (레포 / "pyproject.toml").read_text()
        assert "selectolax" in 의존성 and "httpx[http2]" in 의존성


# ── 파괴적인 것 앞에 선 문 · 시크릿 사정권 (1단계) ──────────────────────────

import re

모든워크플로 = sorted(워크플로.glob("*.yml"))


@pytest.mark.parametrize("이름", sorted(수집들))
def test_수집보다_먼저_원본을_점검한다(이름):
    """⚠️ **유령 DB 를 막는 자리다.** `connect()` 가 부모 디렉터리를 만들고 평범하게 열기
    때문에, 폴더를 옮긴 날 자동 수집은 **새 빈 DB 를 만들고 계속 초록**이며 의원실이
    보는 DB 는 조용히 낡는다 — 감사는 전부 현재 DB 안에서만 등식을 보므로 빈 DB 에서
    다 통과하고, **아무 에러도 나지 않는다.**"""
    글 = 본문(수집들[이름])
    코퍼스 = "congress" if 이름 == "국회" else "law"
    assert f"uv run .claude/skills/k-politics/Scripts/backup.py {코퍼스} --점검" in 글

    점검자리 = 글.index("--점검")
    for 뒤에와야 in ("Scripts/congress/verify.py", "Scripts/law/verify.py",
                     "Scripts/congress/collect.py", "Scripts/law/collect.py"):
        if 뒤에와야 in 글:
            assert 점검자리 < 글.index(뒤에와야), f"{뒤에와야} 가 점검보다 먼저 온다"


@pytest.mark.parametrize("이름", sorted(수집들))
def test_API_키가_잡_전체에_실리지_않는다(이름):
    """⚠️ 잡 레벨 `env` 는 **모든 스텝**에 들어간다 — `upload-artifact` 같은 서드파티
    액션의 프로세스 환경에도 그대로 실린다. 키가 필요한 것은 원천을 두드리는 스텝뿐이다."""
    줄들 = 본문(수집들[이름]).splitlines()
    잡env = False
    for 줄 in 줄들:
        if re.match(r"^    env:", 줄):
            잡env = True
            continue
        if 잡env:
            if not 줄.startswith("      ") or re.match(r"^    \S", 줄):
                잡env = False
                continue
            assert "API_KEY:" not in 줄, f"잡 레벨 env 에 키가 있다: {줄.strip()}"


@pytest.mark.parametrize("이름", sorted(수집들))
def test_커밋된_공개_기본키를_쓴다(이름):
    글 = 본문(수집들[이름])
    assert "secrets.CONGRESS_API_KEY" not in 글
    assert "secrets.LAW_API_KEY" not in 글



@pytest.mark.parametrize("p", 모든워크플로, ids=lambda p: p.name)
def test_액션을_commit_SHA_로_고정한다(p):
    """⚠️ `@v4` 는 **이동 가능한** 태그라 같은 이름이 다른 코드를 가리키게 바뀔 수 있고,
    이 잡들은 self-hosted 라 그 코드가 **내 맥에서** 돈다."""
    안고정 = [줄.strip() for 줄 in 본문(p).splitlines()
              if re.search(r"uses:\s*\S+@(?!\b[0-9a-f]{40}\b)", 줄)]
    assert not 안고정, f"태그로 참조하는 액션이 남아 있다: {안고정}"


@pytest.mark.parametrize("p", 모든워크플로, ids=lambda p: p.name)
def test_권한을_명시한다(p):
    """명시하지 않으면 레포/조직 기본값이 적용되고, 그 기본값은 여기가 아니라 설정
    화면에서 바뀐다 — 워크플로만 읽어서는 무슨 권한으로 도는지 알 수 없게 된다."""
    assert re.search(r"^\s*permissions:", 본문(p), re.M), "permissions 가 없다"


@pytest.mark.parametrize("이름", sorted(수집들))
def test_수집기_DB_경로가_env_에_박혀_있다(이름):
    import yaml

    잡 = yaml.safe_load(본문(수집들[이름]))["jobs"]["collect"]
    for 키, 파일 in (("CONGRESS_DB", "CONGRESS.db"), ("LAW_DB", "LAW.db")):
        assert 잡["env"][키] == str(운영루트 / ".claude/skills/k-politics/DBs" / 파일)


@pytest.mark.parametrize("이름", sorted(수집들))
def test_스냅샷_층이_남아_있지_않다(이름):
    """조회는 수집기 DB 파일을 직접 읽는다 — 중간 스냅샷을 만드는 스텝도, 그 판정을
    종료코드로 전파하는 분기도 없다. 남아 있으면 없는 빌더를 부르는 잡이 된다."""
    assert "snapshot" not in 본문(수집들[이름])


@pytest.mark.parametrize("이름", sorted(수집들))
@pytest.mark.parametrize("수집,예상,경고", [("0", 0, False), ("1", 1, False), ("3", 0, True)])
def test_수집_종료코드와_락_경고가_최종_종료코드에_반영된다(이름, 수집, 예상, 경고):
    import subprocess
    import yaml

    스텝들 = yaml.safe_load(본문(수집들[이름]))["jobs"]["collect"]["steps"]
    판정 = next(s for s in 스텝들 if s.get("name") == "판정 전파")
    assert 판정["if"] == "always()"
    값들 = {"preflight.outcome": "success", "verify.outputs.code": "0", "collect.outputs.code": 수집}

    def 치환(m):
        식 = m[1].strip().removeprefix("steps.")
        키, _, 기본 = 식.partition(" || ")
        return 값들[키] or 기본.strip("'\"")

    셸 = re.sub(r"\$\{\{(.*?)\}\}", 치환, 판정["run"])
    결과 = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", 셸], capture_output=True, text=True)
    assert 결과.returncode == 예상, 결과.stdout + 결과.stderr
    assert ("::warning::" in 결과.stdout) == 경고


def test_백업의_uv_명령도_잠을_막는다():
    명령 = [줄 for 줄 in 명령들(본문(워크플로 / "backup-db.yml")) if "uv run" in 줄]
    assert 명령 and all("caffeinate" in 줄 for 줄 in 명령)
