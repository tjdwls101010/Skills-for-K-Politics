"""국회 수집 락 — 계약은 `tests/락계약.py` 가 지고, 여기서는 배선만 잇는다.

⚠️ **종전 국회 락은 법령이 실측으로 버린 방식(PID + `os.kill`)을 그대로 쓰고 있었고,
   `collect.py` 안에 살아서 다른 진입점은 락을 아예 안 잡았다.** 실측 2026-08-28:
   동시에 들어온 12개가 **전부** 락을 잡았고, PID 를 물려받은 남의 프로세스를 주인으로
   오인했다. 계약을 두 코퍼스가 함께 쓰는 파일에 둔 이유가 그것이다.
"""

from pathlib import Path

import db

import 락계약


class Test수집락(락계약.락계약):
    모듈 = "db"
    스크립트경로 = str(Path(db.__file__).resolve().parent)
    락 = db.락
