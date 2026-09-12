#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27", "pypdf", "olefile"]
# ///
"""진입점 — 본문은 `법제처/직결/커맨드표.py`."""

from 법제처.직결.커맨드표 import _main


if __name__ == "__main__":
    raise SystemExit(_main())
