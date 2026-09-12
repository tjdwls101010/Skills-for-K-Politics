"""빈 표본이 통과로 읽히는 자리 — **언어가 만든 함정이라 자리마다 따로 안 고친다.**

⚠️ `all([])` 도 `set() == set()` 도 `set() <= {...}` 도 전부 **참**이다. 그래서 원천이
빈손으로 오는 날 "전 행에 X 가 있다"·"두 집합이 같다"·"이 값들만 온다" 류의 검사가
**전부 🟢 가 된다** — 표본이 0인 것과 표본이 전부 통과한 것이 같은 색이 되고, 그게 정확히
`verify.py` 가 막으라고 있는 사고다(원천이 조용히 빈손을 주는 것).

실측(2026-08-28)으로 국회 V6·V19·V20·V9b 와 법령 V13·V20 이 그 모양이었다.
"""

import ast
from pathlib import Path

import pytest

from congress import verify

SCRIPTS = Path(verify.__file__).resolve().parent
법령검증 = SCRIPTS.parent / "law" / "verify.py"


@pytest.fixture(autouse=True)
def 결과비우기():
    verify.결과.clear()
    yield
    verify.결과.clear()


class Test표본단언:
    def test_빈_표본은_참이어도_통과가_아니다(self):
        verify.표본단언("VX", "전 행에 코드가 있다", [], True)
        번호, _, ok, 상세 = verify.결과[0]
        assert 번호 == "VX" and ok is False
        assert "표본 0" in 상세, "왜 빨간지가 상세에 없으면 원천 탓인지 우리 탓인지 모른다"

    def test_빈_집합도_통과가_아니다(self):
        """`set() == set()` 과 `set() <= {...}` 이 둘 다 참이라 집합 비교가 특히 위험하다."""
        verify.표본단언("VX", "두 집합이 같다", set(), True)
        assert verify.결과[0][2] is False

    def test_표본이_있고_참이면_통과한다(self):
        verify.표본단언("VX", "무엇", [1, 2, 3], True, "3/3")
        번호, _, ok, 상세 = verify.결과[0]
        assert ok is True and 상세 == "3/3"

    def test_표본이_있고_거짓이면_실패한다(self):
        verify.표본단언("VX", "무엇", [1, 2, 3], False, "2/3")
        assert verify.결과[0][2] is False


class Test단언이_표본을_안_들고_all_을_쓰지_않는다:
    """⚠️ **이건 회귀를 막는 자리다.** 함정을 없앤 뒤 누군가 다시 `단언(..., all(x for ...))`
    을 쓰면 그 검사만 조용히 빈손을 통과시키는데, **통과이므로 아무 신호가 없다.**

    `all(` 만 기계로 잡을 수 있다 — `set() == set()` 이나 `set() <= {...}` 는 겉모습이
    정상 비교와 같아서 못 가른다. 그쪽은 `표본단언` 의 머리말이 지킨다.
    """

    @staticmethod
    def _위반(경로):
        나무 = ast.parse(경로.read_text(encoding="utf-8"))
        나쁜 = []
        for 노드 in ast.walk(나무):
            if not (isinstance(노드, ast.Call) and isinstance(노드.func, ast.Name)
                    and 노드.func.id == "단언"):
                continue
            for 안 in ast.walk(노드):
                if (isinstance(안, ast.Call) and isinstance(안.func, ast.Name)
                        and 안.func.id == "all"
                        and any(isinstance(a, (ast.GeneratorExp, ast.ListComp))
                                for a in 안.args)):
                    나쁜.append(f"{경로.name}:{노드.lineno}")
        return 나쁜

    @pytest.mark.parametrize("경로", [
        pytest.param(SCRIPTS / "verify.py", id="국회 verify.py"),
        pytest.param(법령검증, id="법령 verify.py"),
    ])
    def test_표본을_도는_all_이_맨_단언에_안_남아있다(self, 경로):
        나쁜 = self._위반(경로)
        assert not 나쁜, (
            "빈 표본을 통과시키는 `단언(..., all(... for ...))` 이 남아 있다 "
            "— 표본을 함께 넘기는 `표본단언` 을 써라: " + ", ".join(나쁜)
        )

    def test_검사_자신이_동작한다(self, tmp_path):
        """⚠️ **구조 검사는 조용히 아무것도 안 잡게 되기 쉽다.** 함수 이름을 바꾸거나
        호출 모양이 달라지면 `_위반` 이 0을 돌려주고, 0은 통과와 구별되지 않는다."""
        나쁜파일 = tmp_path / "나쁜.py"
        나쁜파일.write_text(
            '단언("VX", "무엇", all(r.get("K") for r in rows), "상세")\n', encoding="utf-8")
        assert self._위반(나쁜파일), "검사가 명백한 위반을 못 잡는다"
