"""셸 계층 테스트 공용 — 가짜 `gh` 를 PATH 앞에 놓는다.

실제 GitHub API 를 치지 않고도 "무엇을 물었나 / 무엇을 받았을 때 어떻게 판정하나" 를
잰다. 감시자가 조용히 죽어 있었던 것이 이 레포에서 실제로 일어난 일이라,
**감시자 자신에게 회귀 검사가 필요하다.**
"""

import json
import os
import subprocess
from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
스크립트 = 레포 / ".github" / "scripts"

가짜GH = '''#!/usr/bin/env python3
import json, os, pathlib, sys

인자 = " ".join(sys.argv[1:])
기록 = os.environ.get("GH_RECORD")
if 기록:
    with pathlib.Path(기록).open("a", encoding="utf-8") as f:
        f.write(인자 + "\\n")

# ⚠️ **알림이 안 갔는데 잡이 초록이면 그 침묵은 영영 안 들린다.** 어느 gh 부명령이
# 실패했을 때 부르는 쪽이 실제로 비영으로 끝나는지 재려면, 하나씩만 실패시킬 수 있어야 한다.
실패 = os.environ.get("GH_FAIL", "")
if 실패 and 실패 in 인자:
    print(f"gh: {실패} 실패", file=sys.stderr)
    sys.exit(1)

for 조각, 답 in json.loads(os.environ.get("GH_RESPONSES", "{}")).items():
    if 조각 in 인자:
        if 답 != "":
            print(답)
        sys.exit(0)

sys.exit(int(os.environ.get("GH_MISS_EXIT", "0")))
'''


@pytest.fixture
def gh(tmp_path):
    """`(돌린다, 기록읽기)` — 돌린다(스크립트명, 인자들, 응답표, 환경)."""
    빈 = tmp_path / "bin"
    빈.mkdir()
    (빈 / "gh").write_text(가짜GH, encoding="utf-8")
    (빈 / "gh").chmod(0o755)
    기록 = tmp_path / "gh.log"

    def 돌린다(스크립트명, 인자=(), 응답=None, **환경):
        env = {
            **os.environ,
            "PATH": f"{빈}:{os.environ['PATH']}",
            "GH_RESPONSES": json.dumps(응답 or {}, ensure_ascii=False),
            "GH_RECORD": str(기록),
            **환경,
        }
        return subprocess.run(
            [str(스크립트 / 스크립트명), *인자],
            capture_output=True, text=True, errors="replace", env=env,
            # ⚠️ errors="replace" — 셸이 비ASCII 식별자로 죽으면 그 오류 메시지가
            #    UTF-8 이 아닐 수 있고, 그때 테스트가 진단 대신 UnicodeDecodeError 로
            #    죽어 **무엇이 틀렸는지를 가린다.**
        )

    def 기록읽기():
        return 기록.read_text(encoding="utf-8").splitlines() if 기록.exists() else []

    return 돌린다, 기록읽기
