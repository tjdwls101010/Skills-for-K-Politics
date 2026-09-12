"""연결을 열고 **매번** PRAGMA 를 다시 건다.

⚠️ `journal_mode` 는 파일에 저장되지만 **`foreign_keys` 는 연결 속성이고 기본값이 OFF 다.** 수집 스크립트가 각자 별도 프로세스라 실행마다 새 연결이므로, 이 함수를 거치지 않는 경로가 하나라도 있으면 **스키마의 FK 전부가 장식이 된다 — 아무 증상도 없이.**

그래서 `SCHEMA` 에는 PRAGMA 를 넣지 않았다. 두 곳에 적으면 한쪽만 고치게 된다.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[2]   # Scripts/law → Scripts → k-politics
KST = timezone(timedelta(hours=9))

# ⚠️ 해석 순서를 여기서 못박는다. 환경변수가 먼저다 — GitHub Actions 가 DB 를 체크아웃 **밖**으로
#    보내는 유일한 수단이 이것이고, `actions/checkout` 의 `git clean -ffdx` 는 gitignore 된
#    파일까지 지우므로 작업공간 안에 DB 를 두면 매 실행 첫 스텝이 수집물을 날린다. 에러는 안 난다.
DB_ENV = "LAW_DB"


def db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get(DB_ENV)
    return Path(env) if env else SKILL_DIR / "DBs" / "LAW.db"


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    p = db_path(path)
    _유령방지(p, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _유령방지(p: Path, 명시) -> None:
    """**환경변수로 겨눈 DB 는 이미 있어야 한다.** 없으면 만들지 말고 즉시 실패한다.

    ⚠️ 아래 `mkdir(parents=True)` 와 평범한 `connect()` 가 합쳐지면, 폴더를 옮기거나
       경로를 오타 낸 날 자동 수집이 **새 빈 DB 를 만들고 계속 초록으로 끝난다.**
       의원실이 실제로 보는 DB 는 그동안 조용히 낡는데 **아무 에러도 나지 않는다** —
       게이트는 전부 새 DB 안에서 등식을 보므로 빈 DB 에서 다 통과한다.

    환경변수가 있다는 것은 **누군가 특정한 기존 파일을 겨눴다**는 뜻이다(CI 가 DB 를
    체크아웃 밖으로 보내는 유일한 수단이다). 그러니 그 파일이 없는 것은 정상이 아니다.
    거꾸로 `--db` 로 **환경변수와 다른** 경로를 손으로 댄 것은 새로 만들겠다는 뜻일 수
    있으므로 막지 않는다 — **처음 만들 때는 경로를 명시하라**가 곧 그 탈출구다.

    ⚠️ **판정을 '인자가 None 인가'로 하면 안 된다.** 호출자가 `db_path()` 를 먼저 불러
       환경변수를 경로로 바꾼 뒤 그걸 인자로 넘기면(국회 `collect.py` 가 정확히 그랬다)
       가드가 통째로 비껴간다 — **주 수집 경로에만 구멍이 나 있게 된다.** 그래서 묻는 것을
       "어떻게 왔나"가 아니라 **"환경변수가 겨눈 바로 그 파일인가"**로 바꾼다.
    """
    env = os.environ.get(DB_ENV)
    겨눈것 = 명시 is None or (env and Path(env).expanduser() == Path(명시).expanduser())
    if 겨눈것 and env and not p.exists():
        raise SystemExit(
            f"🔴 ${DB_ENV} 가 가리키는 DB 가 없다: {p}\n"
            f"   새 빈 DB 를 만들지 않는다 — 폴더가 옮겨졌거나 경로가 틀렸을 때\n"
            f"   유령 DB 가 생기면 수집은 매일 초록인데 실제 DB 는 조용히 낡는다.\n"
            f"   정말 새로 만들려면 경로를 명시해라: --db {p}"
        )


# ── 락 ──────────────────────────────────────────────────────
#
# ⚠️ **DB 를 고치는 경로는 전부 이걸 거쳐야 한다.** `collect.py` 만 잠그던 시절에는
#    `statute.py`·`precedent.py` 같은 단독 실행이 락 밖에서 `이행()` 을 불렀고, 예약 수집과
#    겹치면 **두 프로세스가 동시에 `DROP COLUMN` 과 테이블 재구축에 들어갈 수 있었다.**
#    그건 되돌릴 방법이 없다. 그래서 락이 DB 를 아는 이 파일에 산다.


class 락:
    """이미 도는 실행이 있으면 **조용히 물러난다.** 죽이거나 락을 깨지 않는다 —
    따라잡기 설계라 선점해서 얻을 것이 없다.

    ⚠️ **끼는 락은 있어선 안 된다.** 물러날 때 종료코드가 0이라, 락이 한 번 끼면
    **워크플로가 매일 초록인 채 수집이 영원히 멈춘다** — 아무도 모른다. 그래서 판정을
    파일 내용이 아니라 **커널에 맡긴다**(`flock`). 커널은 프로세스가 어떻게 죽든
    (SIGKILL·OOM·재부팅) 락을 놓으므로 주인 없는 락이 원리적으로 생기지 않는다.

    ⚠️ **PID 를 읽어 `os.kill(pid, 0)` 로 판정하지 마라.** 둘 다 실측으로 깨진다 —
    죽은 실행의 PID 를 무관한 프로세스가 물려받으면 영원히 물러나고, `exists()` 와
    쓰기 사이가 열려 있어 **동시에 들어온 12개가 전부 락을 잡았다.**

    파일은 지우지 않는다. 지우는 순간 '내가 연 fd 와 지금 파일이 같은 것인가'를 따져야
    하고, 락은 fd 를 닫으면 어차피 풀린다. 내용은 사람이 읽을 진단 기록일 뿐 판정에
    쓰지 않는다.
    """

    def __init__(self, db: str | os.PathLike[str] | None = None):
        self.path = Path(str(db_path(db)) + ".lock")
        self.잡음 = False
        self._fd: int | None = None

    def __enter__(self) -> 락:
        import errno
        import fcntl

        # 락 파일은 DB 옆에 산다. `--db` 로 새 경로를 처음 만들 때 여기가 먼저 오므로
        # 부모를 만들어 준다 — 안 그러면 `db.py init --db 새/경로.db` 가 죽는다.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(self._fd)
            self._fd = None
            # ⚠️ **`OSError` 전부를 「남이 쥐고 있다」로 읽으면 안 된다.** 비차단 `flock` 이
            #    '이미 잡혀 있다'로 주는 것은 EAGAIN/EWOULDBLOCK 뿐이고, ENOLCK·ENOTSUP·EIO
            #    는 **락 장치 자체가 고장 났다**는 뜻이다. 그걸 물러남으로 바꾸면 종료코드
            #    3 → 워크플로 초록이 되어, 락이 안 되는 파일시스템에서 **수집이 매일 조용히
            #    안 도는 채로 초록**이다. 고장은 시끄럽게 끝나야 한다.
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            print(f"이미 도는 실행이 있다 ({self.path.read_text().strip()}). 물러난다.",
                  file=sys.stderr)
            return self
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"pid {os.getpid()} · {datetime.now(KST).isoformat()}\n"
                 .encode())
        self.잡음 = True
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.close(self._fd)   # 닫으면 커널이 락을 놓는다
            self._fd = None


@contextmanager
def 트랜잭션(conn: sqlite3.Connection):
    """진짜 트랜잭션. **`with conn:` 을 쓰지 마라.**

    ⚠️ `connect()` 가 `isolation_level=None`(autocommit)로 열기 때문에 `with conn:` 은
    **트랜잭션을 시작하지 않는다.** 파이썬 sqlite3 의 컨텍스트 매니저는 성공 시 `commit()`,
    실패 시 `rollback()` 을 부를 뿐인데, autocommit 에서는 열린 트랜잭션이 없어 **둘 다
    아무 일도 안 한다.** 즉 블록 중간에 터져도 **이미 쓴 것이 그대로 남는다.**

    실측으로 재현했다 — `with conn:` 안에서 INSERT 후 예외를 던지면 행이 남아 있다.
    그러면 "자식을 지우고 다시 넣는" 저장이 중간에 죽었을 때 **옛 자식만 지워진 반쪽
    레코드가 영구 커밋되고**, 부모가 있다는 이유로 다음 실행이 그걸 건너뛴다.

    ⚠️ **겹쳐 열 수 있다.** 스스로 원자적인 함수(`철회기록` 등)를 더 큰 원자 단위 안에서
    부르는 일이 실제로 있는데, `BEGIN` 을 두 번 치면 `cannot start a transaction within
    a transaction` 으로 죽는다. 안쪽은 SAVEPOINT 로 열어 **바깥 트랜잭션의 일부**가 되게
    한다 — 안쪽만 되감아도 바깥은 살아 있고, 바깥이 되감기면 안쪽도 함께 사라진다.
    """
    if conn.in_transaction:
        conn.execute("SAVEPOINT 중첩")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK TO 중첩")
            conn.execute("RELEASE 중첩")
            raise
        else:
            conn.execute("RELEASE 중첩")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def 읽기연결(path):
    conn = None
    for immutable in (False, True):
        try:
            conn = (sqlite3.connect(path.as_uri() + "?immutable=1", uri=True)
                    if immutable else sqlite3.connect(path))
            # ⚠️ authorizer 뒤에는 PRAGMA 자체가 거부된다. 두 연결 모두 같은 경계를 건다.
            conn.execute("PRAGMA query_only=ON")
            conn.set_authorizer(lambda 작업, *나머지: sqlite3.SQLITE_OK if 작업 in (
                sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                sqlite3.SQLITE_RECURSIVE,   # WITH RECURSIVE — 읽기다(리뷰 실측: 빠지면 not authorized)
            ) else sqlite3.SQLITE_DENY)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone()
            if immutable:
                print("⚠️ immutable 로 열었다 — 마지막 체크포인트 이후 적재(WAL)는 안 보인다",
                      file=sys.stderr)
            return conn
        except sqlite3.Error as e:
            if conn is not None:
                conn.close()
            if immutable or not any(말 in str(e) for 말 in (
                "unable to open database file", "attempt to write a readonly database",
            )):
                raise


