"""국내 보도 코퍼스가 붙어 있나 — 심링크의 감시 검사.

⚠️ **`뉴스.db` 는 다른 레포(Naver-News)의 실체를 가리키는 심링크다.** 우리는 그
파일의 모양을 통제하지 못한다 — 저쪽이 스키마 주석을 다시 `CREATE` 문 **밖**으로
빼거나 상수만 고치고 살아있는 카탈로그에 밀지 않으면, `SKILL.md`의 뉴스 조회 안내가
**조용히 죽는다.** 산문은 안 깨지고 그냥 틀린 안내가 된다. 그래서 감시를 여기 1:1 로 단다.

2026-08-22 까지는 `scripts/newsdb.py schema` 가 정본이라 그 도구도 감시했는데, 저쪽이
개념 주석을 전부 카탈로그로 옮기고 그 서브커맨드를 지웠다(Naver-News #20). 감시하는
명제가 "도구가 더 많이 뱉나"에서 **"`.schema` 가 스스로를 설명하나"** 로 바뀐 이유다.

코퍼스가 없는 머신에서는 통째로 skip 한다. 이 심링크들은 gitignore 되어 있어
클론에는 없는 것이 정상이고, 없는 것을 실패로 부르면 감시가 늑대소년이 된다.
"""

import re
import subprocess
from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
DB = 레포 / ".claude" / "skills" / "k-politics" / "DBs" / "뉴스.db"

pytestmark = pytest.mark.skipif(
    not DB.exists(),
    reason="국내 보도 코퍼스가 이 머신에 없다 (심링크는 gitignore 된다)",
)


def test_schema_가_스스로를_설명한다():
    """기사 단위·수집 완결성·커버리지 설명이 CREATE 문 안에 저장되어야 한다. 문 밖의 주석은 SQLite가 보존하지 않으므로 실제 카탈로그에서 검사한다."""
    결과 = subprocess.run(
        ["sqlite3", f"file:{DB}?mode=rw", "PRAGMA query_only=1;", "SELECT sql FROM sqlite_schema WHERE name IN ('articles', 'cells', 'collection_coverage');"],
        capture_output=True, text=True, timeout=120,
    )
    assert 결과.returncode == 0, 결과.stderr
    schema = 결과.stdout
    assert "CREATE TABLE articles" in schema and "CREATE TABLE cells" in schema
    주석 = len(schema) - len(re.sub(r"--[^\n]*", "", schema))
    assert 주석 > len(schema) * 0.35, f"설명이 {주석}/{len(schema)}자밖에 안 된다"
    # sqlite_schema에 저장된 SQL만 읽으므로 CREATE 문 밖에 둔 주석은 이 검사에 들어올 수 없다.
    for 설명 in ("(oid, aid)", "완결성 원장", "수집한 적 없다", "수집하다 멈췄다"):
        assert 설명 in schema, f"스키마의 의미 설명이 사라졌다: {설명}"


def test_수집_커버리지를_조회로_물을_수_있다():
    """**어떤 카테고리·날짜에 기사가 0건인 것은 세 가지 뜻인데 셋이 똑같이 0으로 보인다** —
    그런 뉴스가 없었거나, 한 번도 수집하지 않았거나, 수집하다 멈췄거나. 마지막이 가장
    위험하다(셀은 complete 이고 감사도 통과라 어디서도 경고가 없다). 저쪽이 그 답을
    산문이 아니라 뷰로 두었으므로 여기서도 뷰가 있는지로 잰다 — 산문은 낡지만 뷰는 안 낡는다.
    """
    r = subprocess.run(
        ["sqlite3", f"file:{DB}?mode=rw", "PRAGMA query_only=1;",
         "SELECT COUNT(*) FROM collection_coverage WHERE last_cell IS NOT NULL;"],
        capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    assert int(r.stdout.strip()) > 0


def test_코퍼스를_읽기_전용으로_열_수_있다():
    """SKILL.md의 mode=rw + query_only 경로로 실제로 연다. WAL 파일이 없는 라이브 DB도 조회되어야 한다."""
    r = subprocess.run(["sqlite3", f"file:{DB}?mode=rw", "PRAGMA query_only=1;", "SELECT COUNT(*) FROM articles;"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-300:]
    assert int(r.stdout.strip()) > 0
