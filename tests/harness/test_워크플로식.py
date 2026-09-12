"""워크플로의 `${{ }}` 식이 GitHub 이 파싱할 수 있는 모양인가.

⚠️ **이 결함은 빨간불을 안 낸다.** 식 문법은 속성 접근에 ASCII 식별자만 받는데,
한글 입력 이름(`inputs.복원검증`)을 쓰면 **워크플로 파일 자체가 무효**가 된다.
무효한 워크플로는 **예약이 아예 안 돌고**, 대신 푸시할 때마다 "invalid workflow file"
실패 실행이 하나씩 생길 뿐이다 — 그 실패는 이름이 그 워크플로라서, 훑어보면
"예약 잡이 돌다가 실패했네"로 읽힌다.

실측 2026-08-28: `backup-db.yml` 이 그 상태로 7번 푸시되는 동안 **예약 백업이 한 번도
안 돌았다.** 그런데 1단계의 파괴적 이행 문이 "24시간 이내 검증본"을 요구하므로, 그
상태가 이어졌으면 스키마를 옮길 일이 생긴 날 **수집이 통째로 멈췄을 것이다.**

이 레포는 식별자에 한글을 쓰는 것이 기본값이라(파이썬·SQL·CLI 전부) 여기서만 예외라는
사실이 눈에 안 띈다. 그래서 사람 기억이 아니라 검사가 진다.
"""

import re
from pathlib import Path

import pytest

워크플로 = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))

_식 = re.compile(r"\$\{\{(.*?)\}\}", re.S)
# 식 안의 문자열 리터럴은 무엇이든 담을 수 있다 — `format('--표본 {0}', …)` 이 정상이다.
_리터럴 = re.compile(r"'[^']*'|\"[^\"]*\"")


def test_검사할_워크플로를_실제로_찾았다():
    assert len(워크플로) >= 4, f"워크플로를 {len(워크플로)}개밖에 못 찾았다"


@pytest.mark.parametrize("p", 워크플로, ids=lambda p: p.name)
def test_식_안의_식별자가_전부_ASCII_다(p):
    나쁜것 = []
    for m in _식.finditer(p.read_text(encoding="utf-8")):
        벗긴것 = _리터럴.sub("''", m.group(1))
        if not 벗긴것.isascii():
            나쁜것.append(m.group(0).strip())
    assert not 나쁜것, (
        f"{p.name} 의 식에 ASCII 아닌 식별자가 있다 — 이 파일은 GitHub 에서 **무효**라\n"
        f"예약이 아예 안 돈다. 이름을 ASCII 로 바꾸고 설명만 한글로 둬라:\n  "
        + "\n  ".join(나쁜것)
    )


@pytest.mark.parametrize("p", 워크플로, ids=lambda p: p.name)
def test_workflow_dispatch_입력_이름이_ASCII_다(p):
    """식에서 안 쓰더라도 이름 자체를 ASCII 로 둔다 — 나중에 식에서 쓰는 순간
    무효가 되는데, 그때는 이 파일을 고친 변경과 증상이 멀어져 있다."""
    import yaml

    문서 = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    # `on:` 은 YAML 1.1 에서 불리언 True 로 읽힌다 — 그래서 키를 둘 다 본다.
    트리거 = 문서.get("on") or 문서.get(True) or {}
    입력들 = ((트리거.get("workflow_dispatch") or {}).get("inputs") or {})
    나쁜것 = [k for k in 입력들 if not str(k).isascii()]
    assert not 나쁜것, f"{p.name} 의 입력 이름이 ASCII 가 아니다: {나쁜것}"
