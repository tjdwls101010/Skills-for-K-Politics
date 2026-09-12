"""모든 스크립트가 **적어도 import 는 된다.**

⚠️ **이 파일이 없어서 `direct.py` 의 문법 오류가 393개 테스트를 전부 통과했다.**
어떤 테스트도 그 모듈을 import 하지 않았기 때문이다. `direct.py` 는 사람이 손으로 부르는
CLI 라 자동 실행에서 안 걸리고, **다음에 누가 부를 때까지 조용히 죽어 있었을 것이다.**

여기서 잠그는 것은 동작이 아니라 **존재**다 — 파싱되는가, `--help` 가 뜨는가.
"""

import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts" / "law"
파일들 = sorted(p.name for p in SCRIPTS.glob("*.py") if "# /// script" in p.read_text(encoding="utf-8"))


def test_스크립트를_하나도_빠뜨리지_않았다():
    """새 스크립트가 생기면 이 목록이 자동으로 늘어난다 — 손으로 적지 않는다."""
    assert set(파일들) == {
        "audit.py", "verify.py", "collect.py", "load.py", "direct.py",
    }


@pytest.mark.parametrize("이름", 파일들)
def test_파싱된다(이름):
    """⚠️ import 가 아니라 **컴파일**을 본다. import 는 `sys.path` 와 의존성에 걸리는데,
    여기서 잡고 싶은 것은 그 전 단계인 문법이다."""
    원문 = (SCRIPTS / 이름).read_text(encoding="utf-8")
    compile(원문, str(SCRIPTS / 이름), "exec")


@pytest.mark.parametrize("이름", ["audit.py", "load.py", "direct.py"])
def test_의존성_없는_CLI_는_help_가_뜬다(이름):
    """네트워크를 안 타는 셋만 본다. `--help` 는 argparse 구성까지 실제로 돌리므로
    모듈 수준 오류와 파서 구성 오류를 함께 잡는다."""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS / 이름), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert r.returncode == 0, r.stderr[-800:]
    assert "usage" in r.stdout.lower()
