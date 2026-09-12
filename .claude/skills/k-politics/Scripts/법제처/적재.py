#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""진입점 — 본문은 `법제처/이행.py`."""

from 법제처.연결 import KST, db_path, connect, 락, now_kst
from 법제처.스키마 import SCHEMA, 드리프트질의, _테이블문, _구조
from 법제처.저장 import record_failure, 메타읽기, 메타쓰기
from 법제처.정리 import 판본묶음
from 법제처.이행 import (
    init_schema, migrate, 이행, 컬럼보강, 스키마버전, 파서버전, _main,
)


if __name__ == "__main__":
    raise SystemExit(_main())
