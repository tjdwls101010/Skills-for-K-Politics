#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""진입점 — 본문은 `law/migrate.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from law.conn import KST, db_path, connect, 락, now_kst
from law.schema import SCHEMA, 드리프트질의, _테이블문, _구조
from law.store import record_failure, 메타읽기, 메타쓰기
from law.prune import 판본묶음
from law.migrate import (
    init_schema, migrate, 이행, 컬럼보강, 스키마버전, 파서버전, _main,
)


if __name__ == "__main__":
    raise SystemExit(_main())
