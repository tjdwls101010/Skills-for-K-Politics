#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""진입점 — 본문은 `law/collectors/run.py`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from law.collectors.run import _main


if __name__ == "__main__":
    raise SystemExit(_main())
