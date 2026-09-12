"""SKILL.md 에도 실측 건수·비율이 살면 안 된다.

⚠️ **금지를 한 표면에만 걸면 수치는 옮겨 갈 뿐이다.** `tests/*/test_*주석수치.py` 가
   스키마 주석에서 건수를 몰아낸 뒤에도 `1,375건`·`88,104건`·`9~14%` 는 SKILL.md 에
   그대로 살아 있었다 — 같은 DB 를 설명하는 두 표면 중 검사가 걸린 쪽만 깨끗해진 것이다.
   수집이 하루 1회 도는 코퍼스에서 건수는 **쓰는 순간부터 낡고**, 지면의 낡은 수치는
   대화에서 현재 사실로 인용된다.

**통합 k-politics 지면을 검사한다.** 국회·법령 코퍼스의 실측 건수는 수집 때마다 낡으므로 지면에 고정하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import 주석수치

지면들 = {"k-politics/SKILL.md": Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "SKILL.md"}


@pytest.mark.parametrize("이름", sorted(지면들))
def test_지면에_실측_건수가_없다(이름):
    주석수치.지면검사(지면들[이름].read_text(encoding="utf-8"), 이름, 허용={})


def test_검사할_지면을_실제로_찾았다():
    """⚠️ 경로가 어긋나면 위 검사가 빈 문자열을 훑고 조용히 초록이 된다."""
    for 이름, 경로 in 지면들.items():
        assert 경로.exists(), f"{이름} 이 {경로} 에 없다"
        assert 경로.read_text(encoding="utf-8").strip(), f"{이름} 이 비었다"
