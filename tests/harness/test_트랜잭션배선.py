"""수집 코드가 `with conn:` 을 쓰지 않는가.

⚠️ **`with conn:` 은 이 레포에서 트랜잭션이 아니다.** 두 코퍼스의 `connect()` 가
`isolation_level=None`(autocommit)로 열기 때문에, 파이썬 sqlite3 의 컨텍스트 매니저가
부르는 `commit()`/`rollback()` 은 **열린 트랜잭션이 없어 둘 다 아무 일도 안 한다.**

그런데 생긴 모양이 트랜잭션이라 읽는 사람은 보호받고 있다고 믿는다. 실측으로 국회
수집의 회의 저장 블록이 정확히 그 상태였다 — 발언 INSERT 가 FK 로 터지면 앞서 넣은
회의 행이 남고, 다음 실행이 `SELECT 회의id FROM 회의` 로 그걸 '이미 했다'로 읽어
**발언 0행인 회의가 영구히 남았다.** 아무 에러도 안 난다.

그래서 문장을 지우는 것으로 끝내지 않고 **구문 자체를 이 레포에서 없앤다.** 남아 있는
한 다음 사람이 다시 쓴다 — 그게 정상적인 파이썬이기 때문이다.
"""

import re
from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
수집코드 = sorted(
    p for 스킬 in ("congress", "law")
    for p in (레포 / ".claude" / "skills" / "k-politics" / "Scripts" / 스킬).glob("*.py")
)

# 문자열·주석 안의 언급은 설명이라 잡지 않는다. 실제 구문만 본다.
_구문 = re.compile(r"^\s*with\s+conn\s*:", re.M)


def test_검사할_파일을_실제로_찾았다():
    """0건을 통과로 읽지 않는다 — 경로가 틀리면 이 파일 전체가 조용히 무의미해진다."""
    assert len(수집코드) >= 8, f"수집 스크립트를 {len(수집코드)}개밖에 못 찾았다"


@pytest.mark.parametrize("p", 수집코드, ids=lambda p: f"{p.parent.parent.name}/{p.name}")
def test_with_conn_을_쓰지_않는다(p):
    줄들 = [i + 1 for i, 줄 in enumerate(p.read_text(encoding="utf-8").splitlines())
            if _구문.match(줄)]
    assert not 줄들, (
        f"{p.relative_to(레포)}:{줄들} 이 `with conn:` 을 쓴다 — autocommit 이라 "
        f"롤백이 없다. `with db.트랜잭션(conn):` (법령은 `with 트랜잭션(conn):`)."
    )
