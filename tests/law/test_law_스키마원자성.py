"""`init_schema` 가 중간에 끊겨도 **뷰가 사라진 DB 가 남지 않는다.**

`SCHEMA` 안의 뷰는 `DROP VIEW IF EXISTS` → `CREATE VIEW` 쌍으로 적힌다. 정의를 고쳤을 때
`CREATE ... IF NOT EXISTS` 로는 갱신이 안 되기 때문이다. 그런데 `executescript()` 는
**문장 하나하나를 따로 커밋한다** — 그 사이에서 끊기면 지우기만 남는다.

⚠️ **1밀리초짜리 경합이 아니다.** 뒤쪽 문장 하나가 실패하기만 해도 앞에서 지운 뷰는
그대로 커밋돼 있다. 그러면 조회가 `no such table` 로 답하고, 그건 다음 수집이 돌 때까지
— 하루 — 이어진다.

⚠️ `executescript()` 는 **열린 트랜잭션도 암묵적으로 커밋한다.** 그래서 `트랜잭션()` 으로
감싸는 것만으로는 아무 일도 일어나지 않는다. 문장 단위로 나눠 넣어야 한다.
"""

from __future__ import annotations

import re
import sqlite3

import pytest

스키마 = pytest.importorskip("law.schema")
이행 = pytest.importorskip("law.migrate")


def _개체(conn) -> set:
    return {
        (r[0], r[1]) for r in conn.execute(
            "SELECT type, name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")
    }


def _뷰(conn) -> set:
    return {n for t, n in _개체(conn) if t == "view"}


def test_스키마가_통째로_적용된다() -> None:
    """⚠️ **이 검사가 먼저다.** 문장을 나눠 넣게 되면 나누기가 조용히 한 문장을 흘릴 수
    있고, 흘린 자리가 인덱스면 조회는 풀스캔이 될 뿐 에러가 안 난다.

    `executescript()` 가 만드는 것과 **같은 개체 집합**인지로 잰다 — 목록을 손으로 적으면
    스키마에 무엇이 더해진 날 그 목록만 낡는다.
    """
    갓 = sqlite3.connect(":memory:", isolation_level=None)
    갓.executescript(스키마.SCHEMA)

    conn = sqlite3.connect(":memory:", isolation_level=None)
    이행.init_schema(conn)

    assert _개체(conn) == _개체(갓)
    assert _뷰(conn), "뷰가 하나도 없으면 아래 검사가 아무것도 안 지킨다"


def test_뒤쪽_문장이_터져도_뷰가_남는다(monkeypatch) -> None:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    이행.init_schema(conn)
    전 = _뷰(conn)

    # ⚠️ **뒤에 붙이면 아무것도 안 잰다.** 뷰의 DROP/CREATE 쌍이 이미 다 끝난 뒤라
    #    그 자리에서 터져도 뷰는 멀쩡하다. 지운 것과 다시 만드는 것 **사이**를 끊어야 한다.
    자리 = re.search(r"DROP VIEW IF EXISTS \S+;", 스키마.SCHEMA)
    assert 자리, "뷰의 DROP/CREATE 쌍이 없으면 이 검사가 아무것도 안 지킨다"
    깨진 = (스키마.SCHEMA[:자리.end()] + "\nTHIS IS NOT SQL;\n"
          + 스키마.SCHEMA[자리.end():])
    monkeypatch.setattr(이행, "SCHEMA", 깨진)
    with pytest.raises(sqlite3.Error):
        이행.init_schema(conn)

    assert _뷰(conn) == 전, "뷰를 지운 것만 커밋됐다 — 조회가 no such table 로 답한다"


def test_두_번_돌려도_같다() -> None:
    """멱등이어야 한다 — 수집마다 맨 앞에서 돈다."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    이행.init_schema(conn)
    전 = _개체(conn)
    이행.init_schema(conn)
    assert _개체(conn) == 전
