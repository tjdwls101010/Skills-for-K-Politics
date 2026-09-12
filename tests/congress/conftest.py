"""테스트 공용 픽스처.

`Scripts/` 를 import 경로에 넣어 `congress` 패키지를 찾는다(PEP 723 인라인 의존성 + `uv run` 방식이라 pyproject 가 없다).
"""

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts"
sys.path.insert(0, str(SCRIPTS))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def db_path(tmp_path):
    """빈 DB 파일 경로. **실제 CONGRESS.db 를 지목할 방법이 없다는 것이 요점이다.**

    초안은 회귀 검사를 `db.py selftest --db <경로>` 로 두려 했는데, 그 설계는 첫 동작이
    대상 DB 삭제라 "실제 DB 를 지목하면 거부" 가드를 따로 짜야 했다(옛 프로젝트 4.25GB 손실).
    `tmp_path` 는 그 위험 자체를 없앤다.
    """
    return tmp_path / "test.db"


@pytest.fixture
def conn(db_path):
    """스키마가 적용된 새 연결. 테스트가 끝나면 닫는다."""
    from congress import db as dbmod

    c = dbmod.connect(db_path)
    dbmod.init_schema(c)
    yield c
    c.close()
