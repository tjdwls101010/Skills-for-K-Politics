"""`audit.py` 가 화면에 내는 모양 — **여기가 아티팩트이자 요약의 원문이다.**

⚠️ **이 파일은 리팩터 앞에 세우는 골든이다.** 곧 `_main` 의 출력부를 떼어내고
   `run(conn)` 을 한 번만 부르게 바꾸는데, 그 뒤에 이 검사를 쓰면 **이미 바뀐 출력을
   골든으로 삼게 되어 아무것도 안 지킨다.** 순서가 이 파일의 전부다.

⚠️ **27줄을 문자열로 통째로 박지 않는다.** 그러면 게이트를 하나 더한 날 검사가 먼저
   빨개지고, 사람은 읽지 않고 새 출력으로 덮는다 — 덮는 순간 골든이 아니다. 대신
   **렌더링의 계약**을 잰다: 게이트 표에서 무엇이 어느 순서로 나오고, 빨간 것에 해석이
   붙고, 마지막 줄이 판정을 말하는가.
"""

from __future__ import annotations

import pytest

감사 = pytest.importorskip("law.audit")



@pytest.fixture
def 출력(db_path, capsys):
    def 돌린다():
        코드 = 감사._main(["--db", str(db_path)])
        return 코드, capsys.readouterr().out
    return 돌린다


def _게이트줄(글: str, 번호: str) -> str:
    return next(줄 for 줄 in 글.splitlines() if 줄.startswith(f"{번호} "))


def test_게이트가_정의된_순서_그대로_한_번씩_나온다(conn, 출력) -> None:
    """순서가 흔들리면 아티팩트를 날짜별로 비교할 수 없다 — 자동 수집의 로그는
    나중에 "언제부터 이상해졌나"에 답하려고 남기는 것이다."""
    conn.close()
    _, 글 = 출력()
    나온순서 = [줄.split()[0] for 줄 in 글.splitlines()
             if 줄[:1] in ("A", "R") and 줄.split()[0] in {n for n, _, _ in 감사.게이트}]
    assert 나온순서 == [번호 for 번호, _, _ in 감사.게이트]


def test_각_게이트_줄이_이름과_값과_신호등을_함께_준다(conn, 출력) -> None:
    """⚠️ **값을 안 재면 이 검사는 아무것도 안 지킨다.** 값이 통째로 사라져도
    이름과 신호등만으로 통과한다 — 리팩터가 포맷 문자열을 건드리기 가장 쉬운 자리다."""
    conn.execute(
        "INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 최초일시, 최종일시)"
        " VALUES ('판례','P1','재시도','x','x'), ('판례','P2','재시도','x','x')")
    conn.commit()
    conn.close()
    _, 글 = 출력()
    줄 = _게이트줄(글, "A3")
    assert "다시 받아야 할 것" in 줄
    assert " 2  🔴" in 줄, f"값이나 신호등이 사라졌다: {줄!r}"
    assert _게이트줄(글, "A2").rstrip().endswith("0  🟢")


def test_빈_DB_는_빨갛고_마지막_줄이_몇_건인지_말한다(conn, 출력) -> None:
    """빈 DB 에서는 A1b(목록 단계를 한 번도 완료 못 한 자료종류)가 빨갛다."""
    conn.close()
    코드, 글 = 출력()
    assert 코드 == 1
    assert "🔴" in _게이트줄(글, "A1b")
    줄들 = [줄 for 줄 in 글.splitlines() if 줄.strip()]
    판정 = next(i for i, 줄 in enumerate(줄들) if 줄.startswith("🔴 게이트 "))
    빨강 = sum(1 for 줄 in 줄들 if 줄.rstrip().endswith("🔴"))
    assert 줄들[판정] == f"🔴 게이트 {빨강}건 위반.", \
        f"판정 줄의 수와 실제 빨간 줄 수가 갈렸다: {줄들[판정]!r} vs {빨강}"
    # ⚠️ **해석은 판정 줄 *뒤*에 온다.** 앞에 두면 빨간 게이트가 여럿일 때 판정이
    #    해석 더미에 밀려 화면 밖으로 나간다. 리팩터가 순서를 뒤집기 쉬운 자리다.
    assert all(줄.strip()[:1] in ("A", "R") for 줄 in 줄들[판정 + 1:])


def test_빨간_게이트에는_해석이_따라_붙는다(conn, 출력) -> None:
    """⚠️ **게이트 이름은 "무엇을 셌나"이지 "그래서 무슨 일이 났나"가 아니다.**
    이 줄은 빨간 순간에만, 공짜로 읽힌다 — 리팩터가 떨어뜨리기 가장 쉬운 자리다."""
    conn.execute(
        "INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 최초일시, 최종일시)"
        " VALUES ('판례','P1','재시도','x','x'), ('판례','P2','재시도','x','x')")
    conn.commit()
    conn.close()
    코드, 글 = 출력()
    assert 코드 == 1
    assert "🔴" in _게이트줄(글, "A3")
    assert any(줄.strip().startswith("A1b:") for 줄 in 글.splitlines()), \
        "빨간 게이트의 해석이 사라졌다"


def test_보고값도_함께_나온다(conn, 출력) -> None:
    """보고값은 판정이 아니지만 **추이를 보는 유일한 자리**라, 판정만 남기고
    떨어뜨리면 빨간불 앞에서 할 수 있는 일이 없어진다."""
    conn.close()
    _, 글 = 출력()
    for 번호, _, _ in 감사.보고:
        assert _게이트줄(글, 번호)


def test_전부_통과하면_초록_한_줄로_끝난다(conn, 출력, monkeypatch) -> None:
    monkeypatch.setattr(감사, "게이트", [("A99", "언제나 0", "SELECT 0")])
    monkeypatch.setattr(감사, "보고", [])
    conn.close()
    코드, 글 = 출력()
    assert 코드 == 0
    끝 = [줄 for 줄 in 글.splitlines() if 줄.strip()][-1]
    assert 끝 == "🟢 게이트 1건 전부 통과."
