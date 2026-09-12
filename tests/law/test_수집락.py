"""DB 락 — 계약은 `tests/락계약.py` 가 지고, 여기서는 배선만 잇는다.

⚠️ 락은 `law/conn.py` 에 산다. `collect.py` 만 잠그던 시절에는 `statute.py` 같은 단독 실행이
락 **밖에서** 스키마를 고쳤고, 예약 수집과 겹치면 두 프로세스가 동시에 `DROP COLUMN`
과 테이블 재구축에 들어갈 수 있었다. **DB 를 고치는 경로는 전부 이걸 거친다.**

⚠️ **계약을 국회와 한 파일에서 나눠 쓴다.** 종전에는 같은 다섯 검사가 이 파일에만
   있었고, 국회는 법령이 실측으로 버린 PID 방식을 그대로 쓰면서 아무도 안 재고 있었다
   — 한쪽만 고쳐진 채 남는 일이 그래서 생긴다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

실행 = pytest.importorskip("law.collectors.run")
연결 = pytest.importorskip("law.conn")
이행 = pytest.importorskip("law.migrate")
저장 = pytest.importorskip("law.store")

import 락계약


class Test수집락(락계약.락계약):
    모듈 = "law.conn"
    스크립트경로 = str(Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts")
    락 = 연결.락


class Test수집_시각_기록:
    """`메타.마지막수집일시` 는 **"우리가 마지막으로 원천을 확인한 때"**다.

    ⚠️ 뜻이 그것이므로 **네트워크를 안 타는 실행이 이 값을 갱신하면 안 된다.**
    `--재파싱` 은 DB 안의 원문만 다시 읽는 작업이라, 그걸로 값이 새로워지면
    **수집기가 며칠째 멈춰 있어도 "방금 확인함"으로 보인다.** 그게 이 값을 남기기로 한
    이유(D39 — 원천은 우리가 언제 받았는지 모른다) 자체를 무의미하게 만든다.
    """

    def test_재파싱은_마지막수집일시를_건드리지_않는다(self, tmp_path):


        db = tmp_path / "재파싱.db"
        conn = 연결.connect(db)
        이행.init_schema(conn)
        저장.메타쓰기(conn, "마지막수집일시", "2020-01-01 00:00:00")
        conn.close()

        실행._main(["--재파싱", "--db", str(db)])   # 네트워크를 안 탄다

        conn = 연결.connect(db)
        assert 저장.메타읽기(conn, "마지막수집일시") == "2020-01-01 00:00:00"
        conn.close()
