"""`tests/` 자신을 import 경로에 넣는다 — 스킬을 가로지르는 계약 모듈(`락계약.py`)을
양쪽 테스트가 같은 것으로 쓰기 위해서다.

⚠️ **레포 루트를 넣는 것이 아니다.** 루트를 넣으면 네 스킬의 `Scripts/` 가 한 `sys.path`
   에서 이름이 겹쳐 먼저 import 된 쪽이 다른 쪽 테스트를 조용히 깨뜨린다(각 스킬
   `conftest.py` 가 그 이유를 적어 두었다). 여기서 올리는 것은 `tests/` 한 디렉터리뿐이다.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(autouse=True)
def _백업마당을_격리한다(tmp_path, monkeypatch):
    """⚠️ **어떤 테스트도 운영자의 진짜 백업 디렉터리를 읽어서는 안 된다.**

    파괴적 경로 앞의 문은 `CORPUS_BACKUP_DIR` 이 없으면 홈의 기본 보관함을 훑는다. 그대로
    두면 테스트 결과가 **그 기계에 마침 무엇이 놓여 있느냐**에 흔들린다 — 백업이 없는
    CI 에서는 초록이고 어제 백업을 뜬 노트북에서는 빨갛다. 더 나쁜 쪽은 그 반대다:
    문이 사라져도 신선한 진짜 세대가 옆에 있으면 테스트가 **통과해 버린다.**

    그래서 기본값을 "빈 마당"으로 못박는다. 세대가 필요한 테스트는 `백업마당` 픽스처로
    직접 놓고, 이 격리를 덮어쓴다.
    """
    monkeypatch.setenv("CORPUS_BACKUP_DIR", str(tmp_path / "빈백업마당"))
