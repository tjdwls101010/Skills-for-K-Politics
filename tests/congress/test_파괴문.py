"""파괴적인 것 앞에 서는 두 문 — 유령 DB 방지와 이행 백업 요구.

⚠️ **둘 다 종전에는 주석이었다.** `connect()` 는 "체크아웃 안에 DB 를 두지 마라"를 주석으로만
말했고 `이행()` 은 "부르기 전에 DB 를 복사해 둬라"를 주석으로만 요구했다. **주석은 아무것도
막지 않는다** — 실측 2026-08-28 기준 이 레포에 백업이 하나도 없는 채로 매일 이행이 돌고 있었다.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import db as dbmod

import 백업픽스처

KST = timezone(timedelta(hours=9))


# ── 유령 DB ─────────────────────────────────────────────────────────────────
#
# ⚠️ `connect()` 가 부모 디렉터리를 만들고 평범하게 열기 때문에, 폴더를 옮기거나 경로를
#    오타 낸 날 자동 수집은 **새 빈 DB 를 만들고 계속 초록으로 끝난다.** 감사는 전부 현재
#    DB 안에서만 등식을 보므로 빈 DB 에서 다 통과하고, 의원실이 실제로 보는 DB 는
#    그동안 조용히 낡는다 — **아무 에러도 나지 않는다.**


class Test유령방지:
    def test_환경변수가_가리키는_DB_가_없으면_만들지_않는다(self, tmp_path, monkeypatch):
        없는것 = tmp_path / "옮겨진곳" / "CONGRESS.db"
        monkeypatch.setenv("CONGRESS_DB", str(없는것))
        with pytest.raises(SystemExit, match="가리키는 DB 가 없다"):
            dbmod.connect()
        assert not 없는것.exists(), "거부해 놓고 파일을 만들었다"
        assert not 없는것.parent.exists(), "거부해 놓고 디렉터리를 만들었다"

    def test_환경변수가_가리키는_DB_가_있으면_연다(self, tmp_path, monkeypatch):
        있는것 = tmp_path / "CONGRESS.db"
        dbmod.connect(있는것).close()          # 명시 경로로는 만들 수 있다
        monkeypatch.setenv("CONGRESS_DB", str(있는것))
        dbmod.connect().close()

    def test_경로를_손으로_대면_새로_만들_수_있다(self, tmp_path, monkeypatch):
        """**처음 만들 때는 경로를 명시하라**가 탈출구다. 환경변수는 '이미 있는 특정
        파일을 겨눴다'는 뜻이라 없는 것이 정상이 아니지만, 손으로 댄 경로는 다르다."""
        monkeypatch.setenv("CONGRESS_DB", str(tmp_path / "없는것.db"))
        새것 = tmp_path / "새로.db"
        dbmod.connect(새것).close()
        assert 새것.exists()

    def test_환경변수가_없으면_예전처럼_만든다(self, tmp_path, monkeypatch):
        monkeypatch.delenv("CONGRESS_DB", raising=False)
        새것 = tmp_path / "기본.db"
        dbmod.connect(새것).close()
        assert 새것.exists()


# ── 이행 백업 문 ────────────────────────────────────────────────────────────


@pytest.fixture
def 백업마당(tmp_path, monkeypatch, db_path):
    """`놓기(...)` — **진짜** 사본을 뜬 세대 하나. 가짜 바이트를 쓰지 않는 이유는
    `tests/백업픽스처.py` 머리말에 있다."""
    보관 = tmp_path / "백업"
    보관.mkdir()
    monkeypatch.setenv("CORPUS_BACKUP_DIR", str(보관))
    return lambda **kw: 백업픽스처.세대만들기("congress", db_path, 보관, **kw)


class Test이행백업문:
    def test_백업이_없으면_이행을_거부한다(self, conn, 백업마당):
        _구조를바꾼다(conn)
        with pytest.raises(SystemExit, match="검증된 백업이 하나도 없다"):
            dbmod.이행(conn)

    def test_거부했으면_테이블을_건드리지_않았다(self, conn, 백업마당):
        """거부가 '중간까지 하고 멈춤'이면 문이 없느니만 못하다."""
        _구조를바꾼다(conn)
        전 = _구조스냅샷(conn)
        with pytest.raises(SystemExit):
            dbmod.이행(conn)
        assert _구조스냅샷(conn) == 전

    def test_신선한_백업이_있으면_돈다(self, conn, 백업마당):
        _구조를바꾼다(conn)
        백업마당()
        assert dbmod.이행(conn)["재구축"] >= 1

    def test_낡은_백업은_통과시키지_않는다(self, conn, 백업마당):
        _구조를바꾼다(conn)
        백업마당(시간전=48)
        with pytest.raises(SystemExit, match="낡았다"):
            dbmod.이행(conn)

    def test_락_없이_뜬_사본은_통과시키지_않는다(self, conn, 백업마당):
        _구조를바꾼다(conn)
        백업마당(락잡음=False)
        with pytest.raises(SystemExit, match="락 없이"):
            dbmod.이행(conn)

    def test_고칠_것이_없으면_백업을_묻지_않는다(self, conn, 백업마당):
        """⚠️ **문은 파괴적인 경로에만 선다.** 마지막 테이블을 재구축한 직후 죽어 백업이
        24시간을 넘긴 상황에서, 이미 목표 구조라 즉시 끝날 재실행이 문에 막히면 안 된다 —
        문이 불변식 2(중단돼도 이어받는다)를 깨뜨린다."""
        assert dbmod.이행(conn) == {"재구축": 0}

    def test_백업확인을_끄면_문이_안_선다(self, conn, 백업마당):
        """테스트가 이행 자체를 재는 길. 운영 경로는 기본값(켜짐)을 쓴다."""
        _구조를바꾼다(conn)
        assert dbmod.이행(conn, 백업확인=False)["재구축"] >= 1


class Test문이곁기록만믿지않는다:
    """⚠️ **곁기록은 과거의 주장이지 지금의 상태가 아니다.** 사본이 저장장치 사고로
    잘려도 JSON 의 `"검증": true` 는 그대로 남는다. 되돌릴 수 없는 일을 시작하기
    직전이므로 **지금 실제로 성한지** 다시 재야 한다."""

    @pytest.mark.parametrize("방식, 조각", [
        ("잘림", "크기가 기록과 다르다"),
        ("내용", "지문이 뜰 때와 다르다"),
    ])
    def test_사고_난_사본으로는_이행을_시작하지_않는다(self, conn, 백업마당, 방식, 조각):
        _구조를바꾼다(conn)
        사본 = 백업마당()
        백업픽스처.망가뜨린다(사본, 방식)
        with pytest.raises(SystemExit, match=조각):
            dbmod.이행(conn)

    def test_곁기록만_남고_사본이_없으면_세지_않는다(self, conn, 백업마당):
        """⚠️ 이 경우 문은 「크기가 다르다」가 아니라 **「하나도 없다」**로 막는다 —
        세대 목록이 디렉터리의 **파일**에서 나오기 때문에, 사본이 사라진 자리의 JSON 은
        애초에 한 표를 얻지 못한다. 장부를 따로 두지 않은 이유가 바로 여기다."""
        _구조를바꾼다(conn)
        백업픽스처.망가뜨린다(백업마당(), "사라짐")
        with pytest.raises(SystemExit, match="검증된 백업이 하나도 없다"):
            dbmod.이행(conn)

    def test_다른_DB_의_백업으로는_열리지_않는다(self, conn, 백업마당, tmp_path, monkeypatch):
        """A 를 백업한 뒤 B 를 이행하면, 실패해도 A 를 복원해 B 를 되돌릴 수 없다."""
        보관 = tmp_path / "백업"
        남의것 = tmp_path / "남의.db"
        다른conn = dbmod.connect(남의것)
        dbmod.init_schema(다른conn)
        다른conn.close()
        백업픽스처.세대만들기("congress", 남의것, 보관)

        _구조를바꾼다(conn)
        with pytest.raises(SystemExit, match="다른 파일의 것이다"):
            dbmod.이행(conn)


class Testmigrate문:
    """`migrate()` 는 `PRAGMA writable_schema=ON` 으로 카탈로그를 직접 고친다 — 함수
    주석이 처음부터 "부르기 전에 DB 를 복사해 둬라"고 요구해 온 자리다. 그런데 문은
    `이행()` 에만 달려 있었고, **국회 수집은 매 실행 `migrate()` 를 부른다.**"""

    def test_주석이_바뀌었으면_백업을_요구한다(self, conn, 백업마당):
        with pytest.raises(SystemExit, match="검증된 백업이 하나도 없다"):
            dbmod.migrate(conn, schema=_주석만바꾼SCHEMA())

    def test_바꿀_것이_없으면_묻지_않는다(self, conn, 백업마당):
        """⚠️ 매 수집이 부르는 함수다. 앞에 두면 **백업이 하루 밀린 날 수집 전체가 멈춘다.**"""
        assert dbmod.migrate(conn) == {"주석교체": [], "구조불일치": []}

    def test_신선한_백업이_있으면_돈다(self, conn, 백업마당):
        백업마당()
        assert dbmod.migrate(conn, schema=_주석만바꾼SCHEMA())["주석교체"]


def _구조스냅샷(conn) -> list:
    return sorted(r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table'"))


def _구조를바꾼다(conn) -> None:
    """`이행()` 이 실제로 할 일이 생기게 만든다 — 한 테이블의 구조를 SCHEMA 와 어긋나게.

    ⚠️ 이게 없으면 `이행()` 이 곧바로 no-op 으로 물러나 **문이 서는지 자체를 못 잰다.**
    """
    conn.execute("DROP TABLE IF EXISTS 의원위원회")
    conn.execute("CREATE TABLE 의원위원회 ("
                 "의원코드 TEXT NOT NULL REFERENCES 의원(의원코드) ON DELETE CASCADE,"
                 "위원회명 TEXT NOT NULL REFERENCES 위원회(위원회명),"
                 "군더더기 TEXT,"
                 "PRIMARY KEY (의원코드, 위원회명))")


def _주석만바꾼SCHEMA() -> str:
    """구조는 그대로 두고 주석만 한 줄 더한 SCHEMA. `migrate()` 가 갈아 끼울 대상이 생긴다."""
    return dbmod.SCHEMA.replace(
        "CREATE TABLE IF NOT EXISTS 의원위원회 (",
        "CREATE TABLE IF NOT EXISTS 의원위원회 (\n    -- 교차 검토가 세운 문을 재는 주석이다.",
        1,
    )
