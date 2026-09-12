"""스키마 주석의 **형식** — 한 주장은 한 줄이다(사용자 결정 2026-09-10).

실측: 주석 300줄 중 139줄이 앞 줄의 이어짐이고 ⚠ 116개 중 91개가 다음 줄로 끊긴다. 모델은 `head`·
`sed -n`·`grep '⚠'` 으로 줄 단위로 읽으므로 경고가 반 토막으로 온다. 규칙: 컬럼 주석은 그 컬럼 아래
한 줄, 표 머리 주석은 문단마다 한 줄, 한 줄에 `--` 하나·⚠ 하나, 임의 폭 줄바꿈 없음. 이것이 바이트
문턱을 고치지는 않는다 — 지도→객체별 상세(`조회.py --스키마`)가 그 답이고, 줄 합치기는 그 위의 형식이다.
"""

from __future__ import annotations

import re
from pathlib import Path

from law import schema as 스키마

A0_스키마문자수 = 48953   # 2026-09-11 기준선: `load.py schema` 출력 바이트 — 지시가 인터페이스로 가면 주석은 줄어야 한다


def _주석줄들():
    return [줄 for 줄 in 스키마.SCHEMA.splitlines() if 줄.lstrip().startswith("--")]


def test_이어지는_줄이_없다():
    """`--   ` 처럼 들여쓴 이어짐 줄은 앞 줄의 주장이 잘린 것이다. 0 이 목표다."""
    이어짐 = [줄 for 줄 in _주석줄들() if re.match(r"^\s*--\s{3,}\S", 줄)]
    assert 이어짐 == [], f"{len(이어짐)}줄이 앞 줄의 이어짐이다:\n" + "\n".join(이어짐[:12])


def test_한_줄에_경고는_하나다():
    둘 = [줄 for 줄 in 스키마.SCHEMA.splitlines() if 줄.count("⚠") > 1]
    assert 둘 == []


def test_표마다_머리_주석이_있다():
    """`--스키마` 지도가 첫 주석 문장을 역할로 뽑는다 — 없으면 첫 컬럼의 주석이 표의 역할로 읽힌다."""
    없음 = []
    for m in 스키마._테이블문.finditer(스키마.SCHEMA):
        본문 = m.group(0).split("(", 1)[1]
        첫줄 = next((l for l in 본문.splitlines() if l.strip()), "")
        if not 첫줄.strip().startswith("--"):
            없음.append(m.group(1))
    assert 없음 == [], f"머리 주석이 없는 표: {없음}"


def test_뷰마다_머리_주석이_있다():
    for m in re.finditer(r"CREATE VIEW (\S+) AS\n(\s*\S+)", 스키마.SCHEMA):
        assert m.group(2).strip().startswith("--"), m.group(1)


def test_스키마_문자수가_기준선을_넘지_않는다(conn):
    stored = "\n".join(r[0] + ";" for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid"))
    assert len(stored.encode("utf-8")) <= A0_스키마문자수, len(stored.encode("utf-8"))


def test_과장을_낳는_표제가_없다():
    for 말 in ("한 번도 판단받지", "한 번도 시험받지", "시험받지 않은 규정"):
        assert 말 not in 스키마.SCHEMA, 말


import pytest


@pytest.mark.parametrize("좌표", [r"audit [AR]\d+", r"감사 [AR]\d+", "정리 유예"])
def test_관리자_좌표를_가리키지_않는다(좌표):
    """읽는 사람은 의원실 일을 하는 클로드다 — `audit.py` 를 돌리는 관리자가 아니다.
    게이트 번호는 그 번호를 아는 사람에게만 뜻이 있으니 원리로 바꾼다."""
    남음 = [줄 for 줄 in 스키마.SCHEMA.splitlines() if re.search(좌표, 줄)]
    assert 남음 == [], "\n".join(남음)


def test_열린_값_집합은_확인하는_법으로_말한다():
    assert "SELECT DISTINCT 위임구분" in 스키마.SCHEMA


def test_수집실패_포인터가_원천_컬럼_이름을_쓴다():
    """`대상종류` 는 사라진 스냅샷 통합표의 이름이었다 — 이 DB 의 컬럼은 `자료종류` 다.
    없는 컬럼을 가리키는 포인터는 따라간 사람을 `no such column` 으로 보낸다."""
    assert "수집실패의 자료종류='위임행정규칙'" in 스키마.SCHEMA
    assert "대상종류" not in 스키마.SCHEMA
