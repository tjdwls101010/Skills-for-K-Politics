#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""진입점 — 본문은 `법제처/수집기/실행.py`."""

from 법제처.수집기.실행 import _main


if __name__ == "__main__":
    raise SystemExit(_main())
