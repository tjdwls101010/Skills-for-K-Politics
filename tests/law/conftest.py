"""law 스킬 테스트 공용 픽스처.

`Scripts/` 를 import 경로에 넣어 `법제처` 패키지를 찾는다(PEP 723 인라인 의존성 + `uv run` 방식이라 pyproject 가 없다).

⚠️ **법제처 패키지에 고유한 이름을 쓰는 이유가 여기 있다.** `tests/congress/conftest.py` 가
congress `Scripts/` 를 같은 `sys.path` 에 이미 올려 두므로, 양쪽이 `net.py`·`db.py` 를
쓰면 한 pytest 실행에서 `import db` 가 어느 쪽인지 갈리고 **먼저 import 된 쪽이
`sys.modules` 를 잡아 다른 스킬의 테스트를 조용히 깨뜨린다.**
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts" / "법제처"
sys.path.insert(0, str(SCRIPTS))



@pytest.fixture
def db_path(tmp_path):
    """빈 DB 파일 경로. **실제 LAW.db 를 지목할 방법이 없다는 것이 요점이다.**

    2GB 를 두 시간 반에 걸쳐 받는 DB 라 테스트가 실수로 그걸 겨누면 복구가 수집 재실행이다.
    `tmp_path` 는 그 위험 자체를 없앤다.
    """
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    """스키마가 적용된 새 연결. 테스트가 끝나면 닫는다."""
    from 법제처 import 연결
    from 법제처 import 이행

    c = 연결.connect(db_path)
    이행.init_schema(c)
    yield c
    c.close()
