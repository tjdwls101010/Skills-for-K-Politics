"""law 스키마 주석에 실측 건수·비율이 다시 들어오면 실패한다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from 법제처 import 스키마
import 주석수치


def test_주석에_실측_건수가_없다():
    주석수치.검사(스키마.SCHEMA, "적재", 허용={})
