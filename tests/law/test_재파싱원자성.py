"""재파싱이 중간에 끊겨도 **참조가 사라진 DB 가 남지 않는다.**

재파싱은 `의율조문`·`인용판례` 를 통째로 비우고 저장된 원문에서 다시 만든다 — 파서를
고쳤을 때 20만 건을 다시 받지 않으려고 뗀 단계다. 그 "비운다"가
**별도 트랜잭션으로 먼저 커밋**되면, 다시 채우는 도중에 무엇이든 터졌을 때 참조가
0행인 DB 가 남는다.

⚠️ **그 상태는 에러로 보이지 않는다.** 다음 수집이 언젠가 다시 채우지만, 그 사이 의원실이
여는 DB 는 `WHERE 자료종류='판례'` 에 **0건**을 주고 0건은 "그 법을 인용한 판례가 없다"로
읽힌다. 불변식 1 — 자동 수집이 DB 를 망가뜨려서는 안 된다.
"""

from __future__ import annotations

import sqlite3

import pytest

스키마 = pytest.importorskip("law.schema")
참조파서 = pytest.importorskip("law.refparser")



def _DB():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(스키마.SCHEMA)
    conn.executemany(
        "INSERT INTO 판례 (판례ID, 사건번호, 참조조문, 수집일시)"
        " VALUES (?, ?, '민법 제750조', 'x')",
        [(str(i), f"2020다{i}") for i in range(1, 6)])
    return conn


def _참조수(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) FROM 의율조문 WHERE 자료종류='판례'").fetchone()[0]


def test_정상_재파싱이_참조를_만든다() -> None:
    """⚠️ **이 검사가 먼저다.** 아래 두 검사는 "행이 그대로다"를 보는데, 애초에 0행이면
    무엇을 해도 통과한다."""
    conn = _DB()
    참조파서.재파싱(conn, 로그=lambda *a: None, 자료=["판례"])
    assert _참조수(conn) == 5


def test_다시_채우다_터져도_옛_참조가_그대로다(monkeypatch) -> None:
    conn = _DB()
    참조파서.재파싱(conn, 로그=lambda *a: None, 자료=["판례"])
    전 = _참조수(conn)

    남은 = [2]

    def 세_번째에_터진다(행들):
        남은[0] -= 1
        if 남은[0] < 0:
            raise RuntimeError("러너가 죽었다")
        return 원래(행들)

    원래 = 참조파서._중복제거
    monkeypatch.setattr(참조파서, "_중복제거", 세_번째에_터진다)

    with pytest.raises(RuntimeError):
        참조파서.재파싱(conn, 로그=lambda *a: None, 자료=["판례"])

    assert _참조수(conn) == 전, (
        "지우기만 커밋되고 다시 채우기가 끊겼다 — 참조가 0건인 DB 가 남는다")


def test_다른_자료종류의_참조까지_함께_지켜진다(monkeypatch) -> None:
    """⚠️ **자료종류마다 트랜잭션을 나누면 절반만 남는다.** 판례는 다시 채워졌는데
    헌재는 지워지기만 한 상태가 그것이고, 그 DB 는 "헌재가 인용한 조문이 없다"고 답한다."""
    conn = _DB()
    conn.execute(
        "INSERT INTO 헌재결정례 (헌재결정례ID, 사건번호, 참조조문, 전문, 수집일시)"
        " VALUES ('9','2020헌바1','민법 제750조','전문', 'x')")
    참조파서.재파싱(conn, 로그=lambda *a: None)
    전 = conn.execute("SELECT 자료종류, COUNT(*) FROM 의율조문 GROUP BY 1").fetchall()
    assert len(전) == 2, f"두 자료종류가 다 파싱돼야 검사가 성립한다: {전}"

    def 터진다(원문):
        raise RuntimeError("러너가 죽었다")

    # ⚠️ `_출처` 는 함수 **객체**를 들고 있어서 모듈 속성을 갈아 끼워도 안 바뀐다.
    monkeypatch.setitem(참조파서._출처, "헌재결정례", [("참조조문", 터진다, "의율조문")])
    with pytest.raises(RuntimeError):
        참조파서.재파싱(conn, 로그=lambda *a: None)

    assert conn.execute(
        "SELECT 자료종류, COUNT(*) FROM 의율조문 GROUP BY 1").fetchall() == 전


def test_파싱_실패가_DB_가_아니라_반환값으로_온다() -> None:
    """⚠️ **표로 담으면 조회자가 매 세션 읽는 `.schema` 에 수집기 내부 사정이 한 표로
    선다.** 그 표를 읽던 코드는 감사 보고값 둘뿐이었고 실측 18,617행 7MB 였다."""
    conn = _DB()
    conn.execute("UPDATE 판례 SET 참조조문='아무 조문도 아닌 문자열' WHERE 판례ID='1'")
    수치, 실패 = 참조파서.재파싱(conn, 로그=lambda *a: None, 자료=["판례"])
    assert 수치["파싱실패"] == 1
    assert [r["자료ID"] for r in 실패] == ["1"]
    assert 실패[0]["사유"] == "패턴 불일치"
    assert "파싱실패" not in {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
