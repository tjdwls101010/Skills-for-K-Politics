#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""진입점 — 본문은 `법제처/검증.py`."""

from 법제처.검증 import main


if __name__ == "__main__":
    raise SystemExit(main())
