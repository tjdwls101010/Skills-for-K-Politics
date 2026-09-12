"""수집 락이 지켜야 하는 계약. **법령과 국회가 같은 것을 쓴다.**

⚠️ **끼는 락은 있어선 안 된다.** 물러날 때 종료코드가 0이던 시절, 락이 한 번 끼면
   워크플로가 매일 초록인 채 수집이 영원히 멈춘다 — 아무도 모른다. 그래서 '주인이
   죽었는데 락이 남는' 경우가 하나도 없어야 하고, 그걸 여기서 잰다.

⚠️ **국회는 종전에 법령이 실측으로 버린 방식을 그대로 쓰고 있었다** — PID 를 읽어
   `os.kill(pid, 0)` 으로 판정. 법령 `load.py` 의 락 주석이 왜 그걸 버렸는지 못박아
   두었는데도 국회에는 그 검사가 없어서, 같은 결함이 한쪽에만 고쳐진 채 남아 있었다.
   **계약을 한 파일에 두면 다음에도 한쪽만 고치는 일이 안 생긴다.**

검사 대상이 커널 동작(프로세스가 죽으면 락이 풀린다)이라 **진짜 자식 프로세스**를 띄운다.
가짜 프로세스로는 애초에 확인이 안 되는 성질이다.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

_쥐고대기 = """
import sys, time
sys.path.insert(0, {스크립트!r})
import {모듈}
with {모듈}.락({경로!r}) as lk:
    print("잡음" if lk.잡음 else "물러남", flush=True)
    time.sleep(60)
"""

_배리어 = """
import sys, time, pathlib
sys.path.insert(0, {스크립트!r})
import {모듈}
출발 = pathlib.Path({출발!r})
while not 출발.exists():
    time.sleep(0.001)
with {모듈}.락({경로!r}) as lk:
    print("잡음" if lk.잡음 else "물러남", flush=True)
    time.sleep(30)
"""


class 락계약:
    """상속해서 `모듈`·`스크립트경로`·`락` 셋만 채운다.

    - `모듈`: 자식 프로세스가 import 할 이름 (`"적재"` · `"db"`)
    - `스크립트경로`: 그 모듈이 있는 디렉터리
    - `락`: 이 프로세스에서 쓸 락 클래스
    """

    모듈: str
    스크립트경로: str
    락: type

    def _자식(self, db: Path) -> subprocess.Popen:
        본 = _쥐고대기.format(스크립트=self.스크립트경로, 모듈=self.모듈, 경로=str(db))
        p = subprocess.Popen([sys.executable, "-c", 본], stdout=subprocess.PIPE, text=True)
        assert p.stdout.readline().strip() == "잡음", "자식이 락을 못 잡았다"
        return p

    def test_이미_도는_실행이_있으면_물러난다(self, tmp_path: Path) -> None:
        db = tmp_path / "코퍼스.db"
        자식 = self._자식(db)
        try:
            with self.락(db) as lk:
                assert not lk.잡음
        finally:
            자식.kill()
            자식.wait()

    def test_SIGKILL_당한_실행의_락은_커널이_푼다(self, tmp_path: Path) -> None:
        """⚠️ **이게 이 계약의 핵심이다.** 정리 코드가 안 도는 죽음이 **기본**이다 —
        OOM·재부팅·러너 취소. 그때 락이 남으면 다음 실행부터 영원히 물러난다."""
        db = tmp_path / "코퍼스.db"
        자식 = self._자식(db)
        자식.kill()
        자식.wait()
        with self.락(db) as lk:
            assert lk.잡음, "SIGKILL 로 죽은 실행의 락이 남았다"

    def test_락_파일이_남아_있어도_주인이_없으면_잡는다(self, tmp_path: Path) -> None:
        db = tmp_path / "코퍼스.db"
        (tmp_path / "코퍼스.db.lock").write_text("99999 2026-08-14T06:00:00+09:00\n")
        with self.락(db) as lk:
            assert lk.잡음

    def test_PID_가_재사용돼도_남의_프로세스를_주인으로_보지_않는다(self, tmp_path: Path) -> None:
        """⚠️ **PID 검사 방식이 여기서 깨진다.** 락을 쥔 실행이 죽고 그 PID 를 무관한
        프로세스가 물려받으면 `os.kill(pid, 0)` 이 살아 있다고 답해 **영원히 물러난다.**
        락 파일에 살아 있는 남의 PID 를 적어 그 상황을 그대로 만든다."""
        db = tmp_path / "코퍼스.db"
        남 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        try:
            (tmp_path / "코퍼스.db.lock").write_text(
                f"{남.pid} 2026-08-14T06:00:00+09:00\n")
            with self.락(db) as lk:
                assert lk.잡음, "죽은 수집의 PID 를 물려받은 남의 프로세스를 주인으로 오인했다"
        finally:
            남.kill()
            남.wait()

    def test_락_장치가_고장_나면_조용히_물러나지_않는다(self, tmp_path: Path,
                                                     monkeypatch) -> None:
        """⚠️ **「남이 쥐고 있다」와 「락이 안 되는 파일시스템이다」는 다른 사건이다.**

        비차단 `flock` 이 '이미 잡혀 있다'로 주는 것은 EAGAIN 뿐이고, ENOLCK·ENOTSUP 은
        락 장치 자체가 고장 났다는 뜻이다. 그걸 물러남으로 바꾸면 종료코드가 「건너뜀」이
        되고 워크플로는 초록인데 **수집은 매일 안 돈다** — 아무도 모른다.
        """
        import errno
        import fcntl

        def 고장(fd, op):
            raise OSError(errno.ENOLCK, "no locks available")

        monkeypatch.setattr(fcntl, "flock", 고장)
        with pytest.raises(OSError):
            with self.락(tmp_path / "코퍼스.db"):
                pass

    def test_부모_디렉터리가_없어도_잡는다(self, tmp_path: Path) -> None:
        """`--db 새/중첩/경로.db` 로 DB 를 처음 만드는 경로. 락이 연결보다 먼저 오므로
        여기서 죽으면 **새 DB 를 아예 못 만든다.**"""
        with self.락(tmp_path / "아직없는" / "폴더" / "코퍼스.db") as lk:
            assert lk.잡음

    def test_동시에_들어와도_한쪽만_잡는다(self, tmp_path: Path) -> None:
        """⚠️ `exists()` 로 보고 `write_text()` 로 쓰는 사이가 열려 있다. 예약 실행과
        사람이 손으로 돌린 것이 겹치면 **둘 다 잡고 같은 DB 에 동시에 쓴다.** 실측으로
        그 방식에서는 동시에 들어온 12개가 **전부** 락을 잡았다.

        자식을 그냥 여러 개 띄우면 인터프리터 기동이 수십 ms 씩 어긋나 창을 못 맞춘다.
        **출발 신호 파일**로 배리어를 걸어 전부 같은 순간에 `__enter__` 로 들어가게 한다.
        """
        db = tmp_path / "코퍼스.db"
        출발 = tmp_path / "출발"
        본 = _배리어.format(스크립트=self.스크립트경로, 모듈=self.모듈,
                            출발=str(출발), 경로=str(db))
        자식들 = [subprocess.Popen([sys.executable, "-c", 본],
                                   stdout=subprocess.PIPE, text=True) for _ in range(12)]
        try:
            time.sleep(2.0)          # 전원이 배리어 앞에 모일 시간
            출발.touch()             # 동시 출발
            time.sleep(1.5)
            for p in 자식들:
                p.kill()
            결과 = [p.stdout.readline().strip() for p in 자식들]
            # ⚠️ **"잡음이 하나"만 세면 느슨하다.** 나머지 11개가 늦게 떠서 배리어에
            #    도착조차 못 했거나 import 로 죽어 빈 줄을 내도 그 단언은 통과한다 —
            #    동시성을 안 재고도 초록이 된다. **전원이 답했는가**를 먼저 본다.
            assert 결과.count("잡음") + 결과.count("물러남") == len(자식들), \
                f"자식 12개 중 답한 것이 {len(자식들) - 결과.count('')}개다: {결과}"
            assert 결과.count("잡음") == 1, \
                f"동시에 {결과.count('잡음')}개가 락을 잡았다: {결과}"
        finally:
            for p in 자식들:
                p.wait()
