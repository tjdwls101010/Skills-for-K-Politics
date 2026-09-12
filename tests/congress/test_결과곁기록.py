"""`collect.py --result` 가 남기는 곁기록 — **워크플로 요약이 읽는 유일한 구조화 출력이다.**

법령 쪽 `tests/law/test_결과곁기록.py` 와 **같은 계약을 잰다.** 키가 갈리면 요약
렌더러가 한쪽만 그린다 — 렌더러는 하나이고 이 둘이 그 입력이다.

⚠️ **곁기록이 없는 종료 경로가 하나라도 있으면 그날 요약은 아무 말도 못 한다.** 그리고
   그런 날은 대개 뭔가 잘못된 날이다 — 원천이 흔들렸거나, 먼저 도는 실행에 막혔거나.
"""

from __future__ import annotations

import json

import pytest

collect = pytest.importorskip("collect")
dbmod = pytest.importorskip("db")
net = pytest.importorskip("net")


class 빈원천:
    def __init__(self, *a, **k): pass
    def __enter__(self): return self
    def __exit__(self, *e): pass
    def close(self): pass
    def all_pages(self, api, **kw): return []
    def json(self, api, **kw): return [], 0


class 못잡는락:
    잡음 = False

    def __init__(self, *a, **kw): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


@pytest.fixture
def 돌린다(conn, db_path, tmp_path, monkeypatch):
    conn.close()
    monkeypatch.setattr(net, "Client", 빈원천)

    def 부른다(*인자):
        곁 = tmp_path / "result.json"
        코드 = collect._main(["--db", str(db_path), "--result", str(곁), *인자])
        assert 곁.exists(), f"곁기록이 안 남았다 (종료코드 {코드})"
        return 코드, json.loads(곁.read_text())
    return 부른다


def test_수집이_끝나면_표와_게이트가_함께_실린다(돌린다) -> None:
    코드, r = 돌린다()
    assert r["코퍼스"] == "congress" and r["판정"] == 코드
    assert r["감사실행"] is True and r["게이트"]
    assert r["표"], "테이블 증분이 안 실렸다 — 요약의 첫 표가 이것이다"
    assert {"이름", "이전", "이후", "증분"} <= set(r["표"][0])
    assert r["종류"] in ("정상", "게이트")


def test_빨간_게이트는_해석까지_실려_나간다(돌린다) -> None:
    """⚠️ **해석은 지금 stdout 에만 있다.** 요약 표만 보는 사람에게는 게이트 이름
    ("무엇을 셌나")만 닿고 "그래서 무슨 일이 났나"는 로그를 뒤져야 한다."""
    코드, r = 돌린다("--audit-only")
    빨강 = [g for g in r["게이트"] if not g["ok"]]
    assert 빨강, "빈 DB 인데 빨간 게이트가 하나도 없다"
    assert r["종류"] == "게이트" and 코드 == 1
    assert any(g["해석"] for g in 빨강), "해석이 붙는 게이트가 하나도 안 실렸다"


def test_락에_막히면_성공과_구별되는_표지가_남는다(돌린다, monkeypatch) -> None:
    """국회는 종료코드 3 으로도 말하지만, **문구를 정하는 것은 `종류` 다** —
    법령은 같은 상황에서 0 을 돌려주므로 코드로는 둘을 같은 규칙으로 못 읽는다."""
    monkeypatch.setattr(dbmod, "락", 못잡는락)
    코드, r = 돌린다()
    assert 코드 == 3
    assert r["종류"] == "락"
    assert r["감사실행"] is False, "감사가 안 돌았는데 돌았다고 적혔다"


def test_원천이_흔들리면_중단으로_남는다(돌린다, monkeypatch) -> None:
    """⚠️ **원천의 딸꾹질을 게이트 위반과 같은 색으로 내보내지 마라.** 종료코드는 이미
    2 로 갈라 두었는데, 요약이 그 구별을 잃으면 사람이 진짜 빨간불도 무시하게 된다."""
    def 터진다(*a, **kw):
        raise net.APIError("가짜")

    monkeypatch.setattr(net, "Client", 터진다)
    코드, r = 돌린다()
    assert 코드 == 2 and r["종류"] == "중단"
    assert r["감사실행"] is False


def test_곁기록을_안_시키면_안_만든다(conn, db_path, tmp_path, monkeypatch) -> None:
    """대화형 실행에 파일을 흘리지 않는다 — 곁기록은 워크플로가 시킬 때만 남는다.

    ⚠️ **작업 디렉터리를 옮겨 놓고 센다.** `tmp_path` 만 보면 구현이 실수로 **현재
    디렉터리에** `result.json` 을 떨어뜨려도 검사가 통과한다.
    """
    conn.close()
    monkeypatch.setattr(net, "Client", 빈원천)
    monkeypatch.chdir(tmp_path)
    collect._main(["--db", str(db_path)])
    assert not list(tmp_path.glob("*.json"))


def test_감사는_실행당_한_번만_돈다(돌린다, monkeypatch) -> None:
    """⚠️ **게이트 하나가 백만 행을 훑는다.** 두 번 도는 것은 그냥 두 배가 아니라,
    그 사이 DB 가 바뀌면 **화면에 낸 표와 곁기록의 값이 갈린다.**"""
    import audit

    센다 = []
    원래 = audit.run
    monkeypatch.setattr(audit, "run", lambda conn: (센다.append(1), 원래(conn))[1])
    돌린다()
    assert len(센다) == 1, f"감사가 {len(센다)}번 돌았다"
