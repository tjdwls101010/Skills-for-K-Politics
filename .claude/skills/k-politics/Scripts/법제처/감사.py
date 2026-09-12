#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""진입점 — 본문은 `법제처/감사.py`."""

from 법제처.감사 import _main


if __name__ == "__main__":
    raise SystemExit(_main())
