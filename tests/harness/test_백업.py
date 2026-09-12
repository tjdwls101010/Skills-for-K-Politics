"""`백업.py` — 이 레포의 유일한 사본을 만드는 곳.

**여기서 재는 것은 "떴다"가 아니라 "무엇을 승격하고 무엇을 거절하나"다.** 백업 코드의 결함은
평소에 아무 증상이 없고 **복구가 필요한 그 한 번**에만 드러나므로, 그 한 번을 여기서 미리 겪는다.

⚠️ 실물 4.3GiB 를 뜨지 않는다. 재는 것이 크기가 아니라 판정이라 작은 DB 로 충분하고,
   `핵심표`·`감사스크립트` 는 실물 레지스트리에서 그대로 온다 — 픽스처가 실물과 갈리면
   테스트만 통과하는 코드가 된다.
"""

import importlib.util
import json
import re
import os
import sqlite3
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
스킬 = 레포 / ".claude" / "skills" / "k-politics"
워크플로 = 레포 / ".github" / "workflows"
운영루트 = Path("/Users/seongjin/Coding/Skills for K-Politics")

# ⚠️ **레포 루트를 `sys.path` 에 올리지 않는다** — 한 pytest 실행에 네 스킬의 모듈이 함께
#    올라와 이름이 겹치고, 먼저 import 된 쪽이 `sys.modules` 를 잡아 다른 쪽을 조용히 깨뜨린다.
#    디렉터리 대신 **파일 하나만** 이름 붙여 올린다.
_명세 = importlib.util.spec_from_file_location("백업", 스킬 / "Scripts" / "backup.py")
sys.modules["백업"] = 백업 = importlib.util.module_from_spec(_명세)
_명세.loader.exec_module(백업)


def 국회DB(경로: Path, *, 의안=3, 발언=2) -> Path:
    """`코퍼스['congress'].핵심표` 를 채운 최소 DB. 표 이름은 레지스트리에서 가져온다."""
    c = sqlite3.connect(경로)
    c.execute("PRAGMA journal_mode=WAL")
    for t in 백업.코퍼스["congress"].핵심표:
        c.execute(f"CREATE TABLE {t} (id INTEGER PRIMARY KEY, v TEXT)")
    for i in range(의안):
        c.execute("INSERT INTO 의안 (v) VALUES (?)", (f"의안{i}",))
    for i in range(발언):
        c.execute("INSERT INTO 발언 (v) VALUES (?)", (f"발언{i}",))
    for t in ("의원", "회의", "표결"):
        c.execute(f"INSERT INTO {t} (v) VALUES ('x')")
    c.commit()
    c.close()
    return 경로


def 감사대역(경로: Path, 종료코드: int, 빨강=(), 연다: bool = False) -> Path:
    """진짜 감사 대신 종료코드를 정해 주는 스크립트. `--db` 계약만 흉내낸다.

    ⚠️ **`연다=True` 가 실물에 가까운 쪽이다.** 진짜 감사는 코퍼스의 `connect()` 로 사본을
    열고, 그 함수는 열면서 `journal_mode=WAL` 을 건다 — 즉 **읽기만 하는 감사가 파일을
    고친다.** 대역이 파일을 아예 안 열면 그 사실이 검사에서 통째로 빠지고, 실제로 그래서
    지문이 깨지는 것을 아무도 못 잡았다.
    """
    본문 = [
        "# /// script",
        '# requires-python = ">=3.11"',
        "# dependencies = []",
        "# ///",
        "import sys",
    ]
    if 연다:
        본문 += [
            "import sqlite3",
            "경로 = sys.argv[sys.argv.index('--db') + 1]",
            "c = sqlite3.connect(경로)",
            "c.execute('PRAGMA journal_mode = WAL')",
            "c.close()",
        ]
    for 줄 in 빨강:
        본문.append(f"print({줄!r} + '  🔴')")
    본문.append(f"sys.exit({종료코드})")
    경로.write_text("\n".join(본문) + "\n", encoding="utf-8")
    return 경로


@pytest.fixture
def 마당(tmp_path, monkeypatch):
    """`(원본, 보관, 감사설정)` — 실물 경로를 절대 안 건드리게 격리한다."""
    원본 = 국회DB(tmp_path / "CONGRESS.db")
    보관 = tmp_path / "백업"
    monkeypatch.setenv("CORPUS_BACKUP_DIR", str(보관))
    monkeypatch.setenv("CORPUS_BACKUP_MIRROR", "")   # 반출하지 마라

    def 감사설정(종료코드, 빨강=(), 연다=False):
        s = 감사대역(tmp_path / "감사대역.py", 종료코드, 빨강, 연다)
        monkeypatch.setitem(백업.코퍼스, "congress",
                            replace(백업.코퍼스["congress"], 감사스크립트=str(s)))

    return 원본, 보관, 감사설정


# ── 승격 ────────────────────────────────────────────────────────────────────


def test_사본을_뜨고_승격한다(마당):
    원본, 보관, _ = 마당
    기록 = 백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)

    assert 기록["검증"] is True
    assert 기록["quick_check"] == "ok"
    세대 = 백업.세대들(백업.코퍼스["congress"], 보관)
    assert len(세대) == 1
    assert 세대[0].경로.exists() and 세대[0].검증
    # 곁기록은 사본 옆에 남는다 — 사본을 지우면 함께 무의미해져야 하기 때문이다.
    assert json.loads(세대[0].곁.read_text())["행수"]["의안"] == 3


def test_사본이_원본과_같은_내용이다(마당):
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    사본 = 백업.세대들(백업.코퍼스["congress"], 보관)[0].경로
    c = sqlite3.connect(f"file:{사본}?immutable=1", uri=True)
    assert [r[0] for r in c.execute("SELECT v FROM 의안 ORDER BY id")] == ["의안0", "의안1", "의안2"]
    c.close()


def test_원본을_바꾸지_않는다(마당):
    """백업 잡이 백업 대상을 수정하면 안 된다. `mode=ro` 가 사 주는 것이 이것뿐이다."""
    원본, 보관, _ = 마당
    전 = (원본.stat().st_size, 원본.stat().st_mtime_ns, 원본.read_bytes())
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert (원본.stat().st_size, 원본.stat().st_mtime_ns, 원본.read_bytes()) == 전


# ── 거절 ────────────────────────────────────────────────────────────────────


def test_핵심표가_비면_승격하지_않는다(tmp_path, 마당):
    """⚠️ `quick_check` 는 **빈 DB 도 통과시킨다.** 구조가 온전한 것과 내용이 있는 것은
    다른 질문이라, 이 검사가 없으면 코퍼스가 사라진 날의 빈 사본이 사다리에 올라간다."""
    _, 보관, _ = 마당
    빈것 = 국회DB(tmp_path / "빈.db", 의안=0, 발언=0)
    sqlite3.connect(빈것).executescript("DELETE FROM 의안; DELETE FROM 발언;")

    with pytest.raises(SystemExit) as e:
        백업.백업("congress", db=빈것, 보관=보관, 감사=False, 락대기초=0)
    assert "0행" in str(e.value)
    assert 백업.세대들(백업.코퍼스["congress"], 보관) == []


def test_곁기록의_행수가_핵심표_밖까지_센다(tmp_path, 마당):
    """⚠️ **곁기록의 `행수` 는 감사가 대참사를 재는 유일한 외부 기준선이다**(`감사 A13`).

    핵심표 다섯만 세면 국회 코퍼스 13개 표 중 여덟이 그 축 밖에 남는다 — 발의자·표결집계·
    의안심사가 통째로 날아가도 직전 정상과 비교할 값 자체가 없어 **감사가 초록이다.**
    `핵심표` 는 "비면 백업이 아니다"를 묻는 다른 질문이라 그대로 두고, 기준선만 넓힌다.
    """
    원본, 보관, _ = 마당
    sqlite3.connect(원본).executescript(
        "CREATE TABLE 발의자 (id INTEGER PRIMARY KEY);"
        "INSERT INTO 발의자 (id) VALUES (1), (2), (3);"
    )
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    세대 = 백업.세대들(백업.코퍼스["congress"], 보관)[-1]
    assert 세대.기록["행수"]["발의자"] == 3
    assert set(백업.코퍼스["congress"].핵심표) <= set(세대.기록["행수"])


def test_핵심표_밖의_표가_비어도_승격한다(tmp_path, 마당):
    """⚠️ **기준선을 넓히는 것과 문을 넓히는 것은 다르다.** 아직 안 채워진 표가 하나
    있다는 이유로 백업이 거절되면, 사고 뒤 복구 중인 DB 가 영영 백업되지 않는다 —
    불변식 2 를 문이 깨뜨린다. '비면 안 된다'는 여전히 `핵심표` 만 묻는다."""
    원본, 보관, _ = 마당
    sqlite3.connect(원본).executescript("CREATE TABLE 발의자 (id INTEGER PRIMARY KEY);")
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    세대 = 백업.세대들(백업.코퍼스["congress"], 보관)[-1]
    assert 세대.기록["행수"]["발의자"] == 0


def test_기준선은_감사통과본을_먼저_고른다(마당):
    """⚠️ **손상본이 기준선이 되면 급감 축이 스스로 무장해제된다.**

    승격을 가르는 것은 구조 검증이고 감사는 라벨이다 — 그래서 코퍼스가 망가진 날의
    사본도 정상적으로 승격된다(그게 옳다; 가장 백업이 필요한 날 사본이 0개가 되면 안 된다).
    그런데 감사가 견줄 '직전 정상' 을 **최신 검증본**으로 잡으면, 사고 다음 백업 한 번으로
    손상 상태가 기준선이 되어 그 다음부터 급감이 0 이다. 사고가 자기 증거를 지운다.
    """
    _, 보관, _ = 마당
    세대놓기(보관, "2026-08-01", 감사위반=0, 행수={"의안": 20_000})
    세대놓기(보관, "2026-08-02", 감사위반=7, 행수={"의안": 1})
    이름, 행수 = 백업.기준선("congress", 보관)
    assert "2026-08-01" in 이름 and 행수["의안"] == 20_000


def test_감사통과본이_없으면_검증본으로_물러나되_그걸_밝힌다(마당):
    """⚠️ **물러나지 않으면 축이 통째로 꺼진다.** 감사가 한 번도 통과한 적 없는 코퍼스
    (법령이 실제로 그 상태였다)에서 기준선이 `None` 이면 대참사 그물이 아예 없다.
    옛 기준선은 급감만 재므로 **더 엄격할 뿐 거짓 초록을 만들지 않는다** — 물러나는 것이
    맞고, 다만 무엇에 견줬는지는 읽는 사람에게 보여야 한다."""
    _, 보관, _ = 마당
    세대놓기(보관, "2026-08-02", 감사위반=7, 행수={"의안": 5})
    이름, 행수 = 백업.기준선("congress", 보관)
    assert 행수["의안"] == 5
    assert "감사" in 이름, f"감사를 통과 못 한 세대에 견줬다는 사실이 안 보인다: {이름}"


def test_행수가_없는_옛_세대는_건너뛴다(마당):
    """`행수` 가 곁기록에 생기기 전 세대가 사다리에 남아 있다 — 그걸 고르면 기준선이 빈다."""
    _, 보관, _ = 마당
    세대놓기(보관, "2026-08-01", 감사위반=0, 행수={"의안": 9})
    세대놓기(보관, "2026-08-02", 감사위반=0, 행수=None)
    이름, 행수 = 백업.기준선("congress", 보관)
    assert "2026-08-01" in 이름 and 행수["의안"] == 9


def test_거절한_사본의_조각을_남기지_않는다(tmp_path, 마당):
    """검증 실패 뒤 4GiB 조각이 남으면 다음 실행의 디스크 preflight 가 그것 때문에 죽는다."""
    _, 보관, _ = 마당
    빈것 = 국회DB(tmp_path / "빈.db", 의안=0, 발언=0)
    with pytest.raises(SystemExit):
        백업.백업("congress", db=빈것, 보관=보관, 감사=False, 락대기초=0)
    assert list((보관 / ".staging").glob("*")) == []


def test_원본이_없으면_실패한다(tmp_path, 마당):
    _, 보관, _ = 마당
    with pytest.raises(SystemExit, match="일반 파일이 아니다"):
        백업.백업("congress", db=tmp_path / "없는것.db", 보관=보관, 감사=False, 락대기초=0)


def test_디스크가_모자라면_DB_를_건드리기_전에_실패한다(마당, monkeypatch):
    원본, 보관, _ = 마당
    import shutil as _shutil

    monkeypatch.setattr(
        백업.shutil, "disk_usage",
        lambda p: _shutil._ntuple_diskusage(total=1, used=1, free=1),
    )
    with pytest.raises(SystemExit, match="여유 공간 부족"):
        백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 백업.세대들(백업.코퍼스["congress"], 보관) == []


# ── 감사는 문이 아니라 라벨이다 ──────────────────────────────────────────────


def test_감사가_빨개도_승격한다(마당):
    """**이 레포에서 가장 중요한 판정이다.** 실측 2026-08-28 LAW 는 게이트 2건이 빨간
    상태였다 — 감사로 승격을 막았다면 가장 백업이 필요한 날에 사본이 0개가 된다."""
    원본, 보관, 감사설정 = 마당
    감사설정(1, 빨강=["A1    적재 건수가 안 맞는 자료종류                    1"])

    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)

    assert 기록["검증"] is True                    # 승격됐다
    assert 기록["감사위반"] == 1                    # 그리고 정직하게 라벨이 붙었다
    assert "위반 1건" in 기록["감사요약"]
    assert 백업.세대들(백업.코퍼스["congress"], 보관)[0].감사통과 is False


def test_감사가_사본을_열어도_지문이_안_깨진다(마당):
    """⚠️ **읽기만 하는 감사가 사본을 고친다.** 코퍼스의 `connect()` 는 열면서
    `journal_mode=WAL` 을 걸어 DB 헤더를 바꾸는데, 지문을 그 앞에서 뜨면 방금 뜬 사본이
    자기 곁기록과 갈린 채로 승격된다. 그러면 그 지문을 근거로 서는 `이행전_확인` 이
    **영원히 거절한다** — 백업을 다시 떠도 열리지 않는 문이라, 파괴적 이행이 통째로 막힌다.

    실측(2026-08-28): LAW 두 세대 모두 헤더가 WAL 로 뒤집혀 있었고 `migrate()` 가
    "사본 지문이 뜰 때와 다르다"로 거부했다."""
    import hashlib
    import os

    원본, 보관, 감사설정 = 마당
    감사설정(0, 연다=True)
    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)

    사본 = Path(기록["경로"])
    assert 기록["감사통과"] is True, \
        "사본을 미리 잠가서 감사가 죽었다 — 라벨이 영영 '못 셌다'가 된다"
    assert hashlib.sha256(사본.read_bytes()).hexdigest() == 기록["sha256"], \
        "감사가 사본을 고쳐서 지문이 깨졌다 — 이행 문이 영원히 닫힌다"
    assert not os.access(사본, os.W_OK), "승격된 사본이 쓰기 가능하다"
    # 문이 실제로 그 지문을 다시 재므로, 여기까지 왔으면 이행도 통과해야 맞는다
    백업.이행전_확인("congress", 원본, 보관=보관)


def test_감사가_통과하면_라벨이_통과다(마당):
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    assert 기록["감사위반"] == 0 and 기록["감사요약"] == "통과"
    assert 백업.세대들(백업.코퍼스["congress"], 보관)[0].감사통과 is True


def test_게이트를_못_세도_빨간_감사를_통과로_읽지_않는다(마당):
    """⚠️ **판정은 종료코드가 지고 세어 본 수는 장식이다.** 표 형식이 바뀌어 빨간 줄을
    하나도 못 세면 위반 수가 0 이 되는데, 그 0 을 「통과」로 읽으면 **감사가 빨간 세대에
    통과 라벨이 붙는다.** 빈 결과를 0 으로 읽어 초록을 내는, 이 레포가 도처에서 당하는 함정이다."""
    원본, 보관, 감사설정 = 마당
    감사설정(1, 빨강=())          # 빨갛게 끝나지만 셀 수 있는 줄이 없다

    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)

    assert 기록["감사통과"] is False
    assert "못 셌다" in 기록["감사요약"]
    assert 백업.세대들(백업.코퍼스["congress"], 보관)[0].감사통과 is False


def test_감사를_건너뛴_세대는_통과로_치지_않는다(마당):
    """모르는 것은 통과가 아니다 — `정리()` 가 이 값으로 '마지막 성한 사본'을 고른다."""
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 백업.세대들(백업.코퍼스["congress"], 보관)[0].감사통과 is False


def test_감사가_죽어도_백업은_승격한다(마당):
    """감사 자신이 깨진 것과 사본이 나쁜 것은 다른 사건이다."""
    원본, 보관, 감사설정 = 마당
    감사설정(3)
    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    assert 기록["검증"] is True
    assert 기록["감사위반"] is None and "죽었다" in 기록["감사요약"]


# ── 락 ──────────────────────────────────────────────────────────────────────


def test_락을_못_잡아도_뜨되_락없음을_남긴다(마당):
    """바쁜 날 백업이 통째로 빠지는 편이 더 나쁘다. 대신 라벨로 정직하게 남긴다."""
    원본, 보관, _ = 마당
    남 = 백업.락(원본)
    assert 남.잡기(0) is True
    try:
        기록 = 백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    finally:
        남.놓기()
    assert 기록["검증"] is True and 기록["락잡음"] is False


def test_락_장치가_고장_나면_락없음으로_적지_않는다(마당, monkeypatch):
    """⚠️ **「남이 쥐고 있다」와 「락이 안 되는 파일시스템이다」는 다른 사건이다.**

    백업은 락을 못 잡아도 뜨고 `락없음` 을 남기는데, 그 라벨을 **이행 문이 읽어
    거절한다.** 그러니 락 장치 자체가 고장 난 것을 `락없음` 으로 적으면 **모든 세대가
    거절 대상이 되고, 백업은 매일 도는데 수집이 멈춘다** — 그것도 조용히.

    같은 결함이 국회·법령 락에도 있었고(`tests/락계약.py` 가 그쪽을 잰다), 여기가
    세 번째 사본이었다.
    """
    import errno
    import fcntl

    원본, 보관, _ = 마당
    monkeypatch.setattr(fcntl, "flock",
                        lambda fd, op: (_ for _ in ()).throw(
                            OSError(errno.ENOLCK, "no locks available")))
    with pytest.raises(OSError):
        백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)


def test_락이_비면_잡는다(마당):
    원본, 보관, _ = 마당
    기록 = 백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 기록["락잡음"] is True


def test_락은_프로세스가_죽어도_풀린다(tmp_path):
    """⚠️ PID 판정으로는 여기가 안 지켜진다 — 죽은 실행의 PID 를 무관한 프로세스가
    물려받으면 영원히 물러난다. `flock` 은 커널이 놓으므로 원리적으로 끼지 않는다."""
    import subprocess

    db = 국회DB(tmp_path / "CONGRESS.db")
    코드 = (
        f"import importlib.util,sys;"
        f"s=importlib.util.spec_from_file_location('백업',{str(스킬 / 'Scripts' / 'backup.py')!r});"
        f"m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        f"L=m.락({str(db)!r});L.잡기(0);import os;os.kill(os.getpid(),9)"
    )
    subprocess.run([sys.executable, "-c", 코드])
    assert 백업.락(db).잡기(0) is True, "SIGKILL 로 죽은 실행의 락이 남았다"


# ── 사다리 ──────────────────────────────────────────────────────────────────


def 세대놓기(보관: Path, 날짜: str, *, 감사위반=0, 이행직전=False, 이름="CONGRESS",
             진짜=True, 행수=...):
    """⚠️ **기본이 진짜 SQLite 파일인 것이 중요하다.** 쓰레기 바이트에 `감사통과: true`
    곁기록만 붙이면, '마지막 성한 사본'을 고르는 코드가 파일을 열어 보는지 아닌지를
    테스트가 구별하지 못한다 — 픽스처가 결함을 숨긴다."""
    보관.mkdir(parents=True, exist_ok=True)
    꼬리 = "-이행직전" if 이행직전 else ""
    p = 보관 / f"{이름}-{날짜}{꼬리}.db"
    if 진짜:
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE t (v)")
        c.commit()
        c.close()
    else:
        p.write_bytes(b"x" * 16)
    곁 = {
        "검증": True, "감사통과": 감사위반 == 0, "감사위반": 감사위반,
        "감사요약": "통과" if 감사위반 == 0 else "위반",
        "락잡음": True, "생성시각": f"{날짜}T03:00:00+09:00",
    }
    # ⚠️ 곁기록에 `행수` 가 아예 없던 옛 세대가 사다리에 남아 있다 — 기본값은 그 모양이
    #    아니라 실물과 같은 모양이라야 한다. `행수=None` 으로 옛 세대를 재현한다.
    if 행수 is not ...:
        if 행수 is not None:
            곁["행수"] = 행수
    else:
        곁["행수"] = {"의안": 100}
    p.with_suffix(".json").write_text(json.dumps(곁, ensure_ascii=False))
    return p


def test_사다리_밖_세대를_지운다(마당):
    _, 보관, _ = 마당
    for d in range(1, 13):
        세대놓기(보관, f"2026-08-{d:02d}")
    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    assert len(남김["남김"]) < 12 and 남김["지움"]
    # 최근 7일은 반드시 남는다
    남은날짜 = {g.날짜 for g in 남김["남김"]}
    assert {"2026-08-12", "2026-08-11", "2026-08-06"} <= 남은날짜


def test_월초는_사다리_밖이어도_남는다(마당):
    _, 보관, _ = 마당
    세대놓기(보관, "2026-03-01")
    for d in range(1, 10):
        세대놓기(보관, f"2026-08-{d:02d}")
    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    assert "2026-03-01" in {g.날짜 for g in 남김["남김"]}


def test_손상이_이어져도_마지막_감사통과본을_밀어내지_않는다(마당):
    """⚠️ **날짜 사다리만 두면 여기가 뚫린다.** 손상이 며칠 이어지면 손상된 세대가 매일
    하루치 자리를 차지해 마지막 성한 사본을 사다리 밖으로 밀어낸다 — 사다리는 '언제'를
    보관하지 '쓸 만한가'를 안 본다."""
    _, 보관, _ = 마당
    세대놓기(보관, "2026-07-15", 감사위반=0)                     # 마지막 성한 것
    for d in range(20, 29):                                      # 그 뒤로 아흐레 내리 손상
        세대놓기(보관, f"2026-08-{d}", 감사위반=4)

    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    assert "2026-07-15" in {g.날짜 for g in 남김["남김"]}


def test_이행직전본은_사다리와_무관하게_남는다(마당):
    _, 보관, _ = 마당
    최근 = (datetime.now(백업.KST) - timedelta(days=30)).strftime("%Y-%m-%d")
    세대놓기(보관, 최근, 이행직전=True)
    for d in range(1, 10):
        세대놓기(보관, f"2026-08-{d:02d}")
    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    assert any(g.이행직전 for g in 남김["남김"])


def test_아주_오래된_이행직전본은_지운다(마당):
    _, 보관, _ = 마당
    옛것 = (datetime.now(백업.KST) - timedelta(days=백업.이행직전보존일 + 5)).strftime("%Y-%m-%d")
    세대놓기(보관, 옛것, 이행직전=True)
    세대놓기(보관, datetime.now(백업.KST).strftime("%Y-%m-%d"))
    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    assert 옛것 not in {g.날짜 for g in 남김["남김"]}


def test_다른_코퍼스의_세대를_건드리지_않는다(마당):
    _, 보관, _ = 마당
    법령것 = 세대놓기(보관, "2020-01-01", 이름="LAW")
    for d in range(1, 12):
        세대놓기(보관, f"2026-08-{d:02d}")
    백업.정리("congress", 보관, 로그=lambda _: None)
    assert 법령것.exists()


# ── 최신검증백업 — `이행()` preflight 이 묻는 것 ────────────────────────────


def test_사본을_지우면_없다고_답한다(마당):
    """⚠️ **DB 안의 메모를 믿으면 여기서 거짓말이 나온다.** 「마지막으로 백업했다」를 메타에
    적어 두면 사본을 지운 뒤에도 그 메모가 남아 파괴적 이행을 통과시킨다."""
    _, 보관, _ = 마당
    p = 세대놓기(보관, datetime.now(백업.KST).strftime("%Y-%m-%d"))
    assert 백업.최신검증백업("congress", 보관) is not None
    p.unlink()
    assert 백업.최신검증백업("congress", 보관) is None


def test_오래된_백업은_신선하지_않다(마당):
    _, 보관, _ = 마당
    세대놓기(보관, "2026-08-01")
    assert 백업.신선한가(백업.최신검증백업("congress", 보관)) is False


def test_방금_뜬_백업은_신선하다(마당):
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 백업.신선한가(백업.최신검증백업("congress", 보관)) is True


def test_곁기록이_없는_파일은_검증본이_아니다(마당):
    """사람이 손으로 놓은 `.db` 를 검증본으로 세지 않는다."""
    _, 보관, _ = 마당
    보관.mkdir(parents=True, exist_ok=True)
    (보관 / "CONGRESS-2026-08-28.db").write_bytes(b"x")
    assert 백업.최신검증백업("congress", 보관) is None


# ── 복원 검증 ───────────────────────────────────────────────────────────────


def test_복원검증이_실제로_열어_감사를_돌린다(마당):
    """「업로드됨」은 백업이 아니다. **다시 열어 감사가 도는 것**만이 근거다."""
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    결과 = 백업.복원검증("congress", 보관=보관, 로그=lambda _: None)
    assert 결과["통과"] is True and 결과["integrity_check"] == "ok"


def test_복원검증은_감사가_빨가면_실패한다(마당):
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    감사설정(1)
    assert 백업.복원검증("congress", 보관=보관, 로그=lambda _: None)["통과"] is False


def test_복원할_세대가_없으면_실패한다(마당):
    _, 보관, _ = 마당
    with pytest.raises(SystemExit, match="복원할 세대가 없다"):
        백업.복원검증("congress", 보관=보관, 로그=lambda _: None)


# ── CLI ─────────────────────────────────────────────────────────────────────


def test_상태는_검증본이_없으면_비영으로_끝난다(마당):
    _, 보관, _ = 마당
    assert 백업._main(["congress", "--보관", str(보관), "--상태"]) == 1


def test_상태는_신선한_검증본이_있으면_영으로_끝난다(마당):
    _, 보관, _ = 마당
    세대놓기(보관, datetime.now(백업.KST).strftime("%Y-%m-%d"))
    assert 백업._main(["congress", "--보관", str(보관), "--상태"]) == 0


def test_db_는_코퍼스를_하나만_지정할_때만_쓴다(마당):
    원본, 보관, _ = 마당
    with pytest.raises(SystemExit):
        백업._main(["--db", str(원본), "--보관", str(보관)])


def test_워크플로가_있고_워치독이_그것을_본다():
    """⚠️ **감시받지 않는 백업은 있다고 믿는 백업이라 없는 것보다 나쁘다.** 옆 레포에서
    백업 잡이 `-wal` 조건 때문에 조용히 죽어 있었고 아무도 몰랐다."""
    assert (워크플로 / "backup-db.yml").is_file()
    글 = (워크플로 / "watchdog.yml").read_text(encoding="utf-8")
    assert "backup-db.yml:success" in 글


def test_백업이_두_수집보다_먼저_돈다():
    """⚠️ **1단계의 `이행()` preflight 이 이 순서에 기대고 있다.** 백업이 수집 뒤로 가면
    "24시간 이내 검증된 백업"을 쥐지 못한 채 파괴적 이행이 도는 창이 매일 열린다."""
    def 분(파일):
        글 = (워크플로 / 파일).read_text(encoding="utf-8")
        m = re.search(r'cron:\s*"(\d+)\s+(\d+)', 글)
        return int(m[2]) * 60 + int(m[1])       # UTC 하루 안의 분

    # 셋 다 같은 UTC 날짜(17:40 · 19:00 · 21:00)에 있으므로 분으로 비교할 수 있다.
    assert 분("backup-db.yml") < 분("collect-congress.yml") < 분("collect-law.yml")


def test_워크플로가_정각을_피한다():
    """실측 8/26 법령 예약이 3시간 23분 밀려 다음 날에 돌았고 그날 자리는 결번이 됐다."""
    글 = (워크플로 / "backup-db.yml").read_text(encoding="utf-8")
    assert not re.search(r'cron:\s*"0\s', 글), "정각은 부하가 몰려 밀린다"


def test_워크플로가_체크아웃_밖의_DB_를_겨눈다():
    """⚠️ `actions/checkout` 이 `git clean -ffdx` 를 도는데 `-x` 가 gitignore 된 파일까지
    지운다. 작업공간을 겨누면 첫 스텝이 수집물을 통째로 날리고 **에러는 나지 않는다.**"""
    글 = (워크플로 / "backup-db.yml").read_text(encoding="utf-8")
    for 키 in ("LAW_DB:", "CONGRESS_DB:", "CORPUS_BACKUP_DIR:"):
        값 = re.search(rf"{키}\s*(\S.*)", 글)[1].strip()
        assert 값.startswith("/"), f"{키} 가 절대 경로가 아니다: {값}"
        assert "${{" not in 값 and "_work" not in 값


@pytest.mark.parametrize(
    "환경변수,상대경로",
    [("LAW_DB", ".claude/skills/k-politics/DBs/LAW.db"),
     ("CONGRESS_DB", ".claude/skills/k-politics/DBs/CONGRESS.db")],
)
def test_백업이_현재_운영루트를_겨눈다(환경변수, 상대경로):
    """DB와 시크릿 검사가 같은 운영 레포를 봐야 경로 이동 뒤에도 함께 살아남는다."""
    글 = (워크플로 / "backup-db.yml").read_text(encoding="utf-8")
    assert f"{환경변수}: {운영루트 / 상대경로}" in 글


def test_워크플로가_잠을_막고_판정을_잃지_않는다():
    글 = (워크플로 / "backup-db.yml").read_text(encoding="utf-8")
    이어붙인 = 글.replace("\\\n", " ")
    긴스텝 = [l.strip() for l in 이어붙인.splitlines() if "uv run .claude/skills/k-politics/Scripts/backup.py" in l and "tee" in l]
    assert 긴스텝 and all("caffeinate" in l for l in 긴스텝), 긴스텝
    # `.claude/skills/k-politics/Scripts/backup.py | tee` 는 셸이 tee 의 0 을 돌려주므로 실패가 초록으로 덮인다.
    assert "set -o pipefail" in 글 and "PIPESTATUS" in 글
    assert "permissions:\n  contents: read" in 글


def test_판정_전파는_언제나_돈다():
    """앞 스텝이 죽으면 판정 스텝이 아예 실행되지 않아 실패가 초록으로 끝날 수 있다."""
    글 = (워크플로 / "backup-db.yml").read_text(encoding="utf-8")
    뒤 = 글.split("name: 판정 전파", 1)[1]
    assert 뒤.lstrip().startswith("if: always()")


def test_한_코퍼스가_실패해도_나머지는_뜬다(마당, monkeypatch, tmp_path):
    """⚠️ 둘을 묶으면 법령이 실패한 날 국회 사본까지 함께 잃는다."""
    원본, 보관, _ = 마당
    monkeypatch.setitem(
        백업.코퍼스, "law",
        replace(백업.코퍼스["law"], 스크립트=tmp_path / "없는것.py"),
    )
    monkeypatch.setenv("CONGRESS_DB", str(원본))
    assert 백업._main(["--보관", str(보관), "--감사-생략", "--락대기", "0"]) == 1
    assert len(백업.세대들(백업.코퍼스["congress"], 보관)) == 1


# ══════════════════════════════════════════════════════════════════════════
# 교차 검토(codex, gpt-5.6-sol)가 찾아낸 아홉 가지. **하나하나가 "평소엔 아무 증상이
# 없고 복구가 필요한 그 한 번에만 드러나는" 종류라 여기에 회귀 검사를 둔다.**
# ══════════════════════════════════════════════════════════════════════════


def test_같은_날_재실행이_감사통과본을_덮지_않는다(마당):
    """⚠️ **[1] 가장 위험했던 것.** 02:40 에 감사까지 통과한 사본을 떠 놓고, 그날 수집이
    데이터를 망가뜨린 뒤 백업을 한 번 더 돌리면 — 구조 검증만 통과하면 승격되므로
    **가장 필요한 그 사본이 바로 그 순간 사라졌다.** `정리()` 의 '마지막 성한 사본을
    남긴다'는 이미 파일이 덮인 뒤라 아무 도움이 안 된다."""
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    첫번째 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)["경로"]
    지문 = json.loads(Path(첫번째).with_suffix(".json").read_text())["sha256"]

    감사설정(1, 빨강=["A1  망가졌다  1"])
    두번째 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)["경로"]

    assert 두번째 != 첫번째, "감사 실패본이 감사 통과본을 덮었다"
    assert Path(첫번째).exists()
    assert json.loads(Path(첫번째).with_suffix(".json").read_text())["sha256"] == 지문
    assert "#2" in Path(두번째).name


def test_감사통과본은_감사통과본으로_덮인다(마당):
    """덮지 않는 규칙은 '더 나쁜 것이 더 나은 것을 밀어내지 않는다'이지 '절대 덮지
    않는다'가 아니다. 매일 도는 백업이 날마다 파일을 불리면 사다리가 무의미해진다."""
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    첫번째 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)["경로"]
    두번째 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)["경로"]
    assert 첫번째 == 두번째
    assert len(백업.세대들(백업.코퍼스["congress"], 보관)) == 1


def test_백업_둘이_겹치면_뒤에_온_쪽이_물러난다(마당):
    """⚠️ **[2]** Actions 의 `concurrency` 는 같은 워크플로 실행끼리만 직렬화한다 —
    직접 실행·이행직전 백업은 막지 못하고, 겹치면 한쪽이 **다른 쪽이 아직 쓰고 있는
    조각을 승격**할 수 있었다."""
    원본, 보관, _ = 마당
    보관.mkdir(parents=True, exist_ok=True)
    앞선것 = 백업.락(보관 / ".CONGRESS.백업")
    assert 앞선것.잡기(0)
    try:
        with pytest.raises(SystemExit, match="이미 돌고 있다"):
            백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    finally:
        앞선것.놓기()
    # 물러난 뒤에는 다시 잡을 수 있어야 한다 — 락이 끼면 백업이 영원히 안 돈다.
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)


def test_살아_있는_조각을_지우지_않는다(마당):
    """나중에 시작한 실행이 먼저 시작한 실행의 조각을 unlink 하면, 앞 실행은 사라진
    inode 를 계속 쓰고 승격할 때 엉뚱한 파일을 올린다."""
    원본, 보관, _ = 마당
    스테이징 = 보관 / ".staging"
    스테이징.mkdir(parents=True, exist_ok=True)
    살아있음 = 스테이징 / "CONGRESS-2026-08-28.99999.partial"
    살아있음.write_bytes(b"x")
    낡음 = 스테이징 / "CONGRESS-2026-08-01.11111.partial"
    낡음.write_bytes(b"x")
    os.utime(낡음, (0, 0))

    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)

    assert 살아있음.exists(), "지금 도는 다른 실행의 조각을 지웠다"
    assert not 낡음.exists(), "여섯 시간 넘은 조각은 치워야 한다"


def test_반출이_전부_실패하면_옛_반출본을_지우지_않는다(마당, tmp_path, monkeypatch):
    """⚠️ **[3]** 종전에는 '보낼 예정이던 이름'으로 유지 집합을 만들어서, 용량 부족이나
    I/O 오류로 복사가 **전부 실패해도** 옛 반출본을 지웠다 — 반출본이 0개가 되고 그
    사실이 종료코드에도 안 실려 워크플로와 워치독은 초록이었다."""
    원본, 보관, _ = 마당
    밖 = tmp_path / "icloud"
    밖.mkdir()
    옛것 = 밖 / "CONGRESS-2026-08-26.db"
    옛것.write_bytes("옛 반출본".encode())

    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    monkeypatch.setattr(백업.shutil, "copyfile",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("ENOSPC")))
    보낸것 = 백업.반출("congress", 보관, 밖, 로그=lambda _: None)

    assert 보낸것 == []
    assert 옛것.exists(), "복사가 전부 실패했는데 옛 반출본을 지웠다"


def test_반출은_크기가_같아도_지문이_다르면_다시_보낸다(마당, tmp_path):
    """⚠️ **[9]** SQLite 는 페이지 단위라 내용이 바뀌어도 크기가 같은 경우가 흔하다.
    크기만 보면 같은 크기로 손상된 반출본이 영원히 안 고쳐진다."""
    원본, 보관, _ = 마당
    밖 = tmp_path / "icloud"
    기록 = 백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    백업.반출("congress", 보관, 밖, 로그=lambda _: None)

    저쪽 = 밖 / Path(기록["경로"]).name
    원래크기 = 저쪽.stat().st_size
    저쪽.write_bytes(b"\0" * 원래크기)                       # 크기는 같고 내용만 썩었다
    (밖 / (저쪽.stem + ".json")).write_text(json.dumps({"sha256": "다른것"}))

    백업.반출("congress", 보관, 밖, 로그=lambda _: None)
    assert 저쪽.read_bytes() != b"\0" * 원래크기, "크기가 같다고 건너뛰었다"


def test_승격_직전에_옛_곁기록을_치운다(마당):
    """⚠️ **[4]** DB 를 먼저 바꾸면 그 직후 죽었을 때 새 사본 옆에 옛 판정이 남는다 —
    감사가 빨간 사본이 「통과」로 읽힌다. 이 순서면 그 창에서 남는 것은 '곁기록 없는
    사본' = 미검증이라 사고가 안전한 쪽으로 넘어진다."""
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    첫 = Path(백업.백업("congress", db=원본, 보관=보관, 락대기초=0)["경로"])

    본것 = []
    진짜 = 백업.Path.replace

    def 엿본다(self, 대상):
        if str(self).endswith(".partial"):
            본것.append(Path(대상).with_suffix(".json").exists())
        return 진짜(self, 대상)

    감사설정(0)
    import unittest.mock
    with unittest.mock.patch.object(백업.Path, "replace", 엿본다):
        백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    assert 본것 == [False], "승격하는 순간 옛 곁기록이 아직 남아 있었다"


def test_썩은_파일은_마지막_성한_사본으로_치지_않는다(마당):
    """⚠️ **[5]** 저장장치에서 썩은 파일의 곁기록에도 `감사통과: true` 는 그대로 남아
    있다. 그걸 골라 놓고 진짜 성한 옛 사본을 지우면 돌아갈 곳이 없다."""
    _, 보관, _ = 마당
    진짜성한것 = 세대놓기(보관, "2026-07-15", 감사위반=0)
    세대놓기(보관, "2026-08-28", 감사위반=0, 진짜=False)          # 곁기록만 초록
    for d in range(19, 28):
        세대놓기(보관, f"2026-08-{d}", 감사위반=4)

    백업.정리("congress", 보관, 로그=lambda _: None)
    assert 진짜성한것.exists(), "썩은 파일을 성한 것으로 믿고 진짜를 지웠다"


def test_hot_journal_이_있으면_평범하게_열지_않는다(tmp_path):
    """⚠️ **[6]** `-wal` 만 보면 롤백 저널 모드의 hot journal 을 놓친다 — 그때 평범한
    연결이 **저널 복구를 수행하며 원본을 고친다.** 백업 잡이 백업 대상을 수정하는 사고다."""
    db = tmp_path / "x.db"
    국회DB(db)
    Path(str(db) + "-journal").write_bytes(b"hot journal")

    import unittest.mock
    with unittest.mock.patch.object(
        백업.sqlite3, "connect",
        side_effect=백업.sqlite3.OperationalError("unable to open database file"),
    ):
        with pytest.raises(RuntimeError, match="-journal"):
            백업._원본URI(db)


def test_감사가_도는_동안_수집_락을_붙들지_않는다(마당):
    """⚠️ **[7]** 감사까지 락을 쥐면 최대 30분이다. 예약이 밀려 수집과 겹치면 수집기가
    락을 못 잡고 물러나며 **종료코드 0** 을 낸다 — 수집하지 않은 날이 초록으로 보인다."""
    원본, 보관, 감사설정 = 마당
    잡혔나 = []
    s = 감사대역(원본.parent / "감사대역.py", 0)
    엿보기 = 원본.parent / "락엿보기.py"
    엿보기.write_text(
        "# /// script\n# requires-python = \">=3.11\"\n# dependencies = []\n# ///\n"
        "import fcntl, os, sys\n"
        f"fd = os.open({str(원본) + '.lock'!r}, os.O_CREAT | os.O_RDWR, 0o644)\n"
        "try:\n"
        "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "    print('락이 비어 있다')\n"
        "except OSError:\n"
        "    print('락을 아직 쥐고 있다'); sys.exit(7)\n"
        "sys.exit(0)\n", encoding="utf-8")
    from dataclasses import replace as _replace
    import unittest.mock
    with unittest.mock.patch.dict(
        백업.코퍼스,
        {"congress": _replace(백업.코퍼스["congress"], 감사스크립트=str(엿보기))},
    ):
        기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    assert 기록["감사통과"] is True, "감사가 도는 동안 수집 락이 아직 잡혀 있었다"
    assert s.exists()


def test_뜨는_사이_정상적으로_행이_줄어도_사본을_버리지_않는다(마당, monkeypatch):
    """⚠️ **[8]** 원본 행수를 VACUUM 앞에서만 읽으면, 그 사이 정리·이행이 정상적으로
    행을 지웠을 때 **온전한 사본을 거절한다.** 하한은 전후의 작은 쪽이어야 한다."""
    원본, 보관, _ = 마당
    진짜행수 = 백업._행수

    호출 = {"n": 0}

    def 줄어든다(uri, 표, *, uri모드=True):
        호출["n"] += 1
        결과 = 진짜행수(uri, 표, uri모드=uri모드)
        if 호출["n"] == 1:          # VACUUM 앞의 원본 — 아직 3행
            return 결과
        if 호출["n"] == 2:          # VACUUM 뒤의 원본 — 그 사이 1행이 지워졌다
            return {**결과, "의안": 1}
        return {**결과, "의안": 2}  # 사본은 그 중간 시점

    monkeypatch.setattr(백업, "_행수", 줄어든다)
    기록 = 백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 기록["검증"] is True


def test_지문이_다르면_복원_검증이_실패한다(마당):
    """`integrity_check` 는 SQLite 구조만 본다 — 뜬 뒤에 바이트가 바뀌어도 구조가
    성하면 통과한다. 지문이 갈리면 그 자체가 사건이다."""
    원본, 보관, 감사설정 = 마당
    감사설정(0)
    기록 = 백업.백업("congress", db=원본, 보관=보관, 락대기초=0)
    곁 = Path(기록["경로"]).with_suffix(".json")
    d = json.loads(곁.read_text())
    d["sha256"] = "0" * 64
    곁.write_text(json.dumps(d, ensure_ascii=False))

    결과 = 백업.복원검증("congress", 보관=보관, 로그=lambda _: None)
    assert 결과["sha256일치"] is False and 결과["통과"] is False


def test_같은_날_두_세대가_사다리_두_칸을_먹지_않는다(마당):
    """사다리를 파일로 세면 하루가 두 칸을 먹어 **보관 기간이 조용히 절반으로 줄어든다.**"""
    _, 보관, _ = 마당
    세대놓기(보관, "2026-08-01")
    for d in range(20, 29):
        세대놓기(보관, f"2026-08-{d}")
    # 8/28 에 같은 날 두 번째 세대를 놓는다
    두번째 = 보관 / "CONGRESS-2026-08-28#2.db"
    sqlite3.connect(두번째).close()
    두번째.with_suffix(".json").write_text(json.dumps(
        {"검증": True, "감사통과": True, "생성시각": "2026-08-28T09:00:00+09:00"}))

    남김 = 백업.정리("congress", 보관, 로그=lambda _: None)
    남은날짜 = {g.날짜 for g in 남김["남김"] if not g.이행직전}
    assert len([d for d in 남은날짜 if d.startswith("2026-08-2")]) >= 7


# ── 이행 앞에 서는 문 · 수집 앞에 서는 점검 ─────────────────────────────────


def test_이행전_확인은_백업이_없으면_거부한다(마당):
    원본, 보관, _ = 마당
    with pytest.raises(SystemExit, match="검증된 백업이 하나도 없다"):
        백업.이행전_확인("congress", 원본, 보관=보관)


def test_이행전_확인은_낡은_백업을_거부한다(마당):
    원본, 보관, _ = 마당
    세대놓기(보관, "2026-08-01")
    with pytest.raises(SystemExit, match="낡았다"):
        백업.이행전_확인("congress", 원본, 보관=보관)


def test_이행전_확인은_락없이_뜬_사본을_거부한다(마당):
    """반쯤 이행된 상태의 사본을 근거로 또 이행에 들어가면 되돌릴 곳이 되돌릴 수 없다."""
    원본, 보관, _ = 마당
    남 = 백업.락(원본)
    남.잡기(0)
    try:
        백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    finally:
        남.놓기()
    with pytest.raises(SystemExit, match="락 없이"):
        백업.이행전_확인("congress", 원본, 보관=보관)


def test_이행전_확인은_신선한_검증본을_통과시킨다(마당):
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 백업.이행전_확인("congress", 원본, 보관=보관).검증 is True


def test_이행전_확인은_다른_DB_를_뜬_사본을_거부한다(마당, tmp_path):
    """⚠️ **코퍼스 이름은 어느 사다리를 볼지만 정한다.** A 를 백업한 뒤 `CONGRESS_DB` 를
    B 로 바꾸면, 이름만 보는 문은 A 의 신선한 사본을 근거로 **B 를 파괴한다.** 실패한 뒤
    A 를 복원해도 B 는 돌아오지 않는다 — 무엇을 지키는지는 경로가 정해야 한다."""
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    남의것 = tmp_path / "남의.db"
    남의것.write_bytes(원본.read_bytes())
    with pytest.raises(SystemExit, match="다른 파일의 것이다"):
        백업.이행전_확인("congress", 남의것, 보관=보관)


def test_점검은_DB_가_없으면_빨갛다(마당, tmp_path):
    _, 보관, _ = 마당
    assert 백업.점검("congress", db=tmp_path / "없다.db", 보관=보관, 로그=lambda _: None) == 1


def test_점검은_직전_세대의_절반_미만이면_빨갛다(마당, tmp_path):
    """⚠️ **유령 DB 를 잡는 자리다.** 폴더를 옮긴 날 자동 수집은 새 빈 DB 를 만들고 계속
    초록이며, 의원실이 보는 DB 는 조용히 낡는다 — 아무 에러도 나지 않는다."""
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    쪼그라듦 = tmp_path / "유령.db"
    국회DB(쪼그라듦, 의안=0, 발언=0)
    쪼그라듦.write_bytes(쪼그라듦.read_bytes()[:4096])

    assert 백업.점검("congress", db=쪼그라듦, 보관=보관, 로그=lambda _: None) >= 1


def test_점검은_정상이면_초록이다(마당):
    원본, 보관, _ = 마당
    백업.백업("congress", db=원본, 보관=보관, 감사=False, 락대기초=0)
    assert 백업.점검("congress", db=원본, 보관=보관, 로그=lambda _: None) == 0
