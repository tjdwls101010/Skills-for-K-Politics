"""`db.트랜잭션` — 되돌릴 수 있는 블록.

⚠️ 이 함수가 존재하는 이유 전체가 **`connect()` 가 autocommit 으로 연다**는 한 가지
사실 위에 서 있다. 그 전제를 아래 첫 테스트가 잠근다 — 전제가 바뀌면 `with conn:` 이
갑자기 진짜 트랜잭션이 되고, 이 파일의 나머지가 무엇을 지키는지도 달라진다.
"""

import sqlite3

import pytest

import db as dbmod


def test_연결이_autocommit_이다(conn):
    """설계의 전제. `isolation_level=None` 이라 파이썬이 BEGIN 을 안 넣는다."""
    assert conn.isolation_level is None


def test_with_conn_은_되돌리지_않는다(conn):
    """⚠️ **이 레포가 실제로 당한 것을 그대로 재현한다.** 생긴 모양은 트랜잭션인데
    autocommit 에서는 `rollback()` 이 되돌릴 열린 트랜잭션 자체가 없다."""
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('가짜위')")
            raise RuntimeError("여기서 죽었다")
    남은것 = conn.execute(
        "SELECT COUNT(*) FROM 위원회 WHERE 위원회명='가짜위'").fetchone()[0]
    assert 남은것 == 1, "`with conn:` 이 되돌렸다면 이 파일의 전제가 바뀐 것이다"


def test_트랜잭션은_되돌린다(conn):
    conn.execute("DELETE FROM 위원회")
    with pytest.raises(RuntimeError):
        with dbmod.트랜잭션(conn):
            conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('가짜위')")
            raise RuntimeError("여기서 죽었다")
    assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 0


def test_트랜잭션은_끝까지_가면_커밋한다(conn):
    conn.execute("DELETE FROM 위원회")
    with dbmod.트랜잭션(conn):
        conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('진짜위')")
    assert conn.execute(
        "SELECT COUNT(*) FROM 위원회 WHERE 위원회명='진짜위'").fetchone()[0] == 1


def test_KeyboardInterrupt_도_되돌린다(conn):
    """⚠️ `except Exception` 으로 잡으면 여기가 샌다. 자동 수집을 사람이 Ctrl-C 로
    끊는 것은 정상 사건이고, 그때 반쪽 회의가 남으면 다음 실행이 건너뛴다."""
    conn.execute("DELETE FROM 위원회")
    with pytest.raises(KeyboardInterrupt):
        with dbmod.트랜잭션(conn):
            conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('가짜위')")
            raise KeyboardInterrupt
    assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 0
