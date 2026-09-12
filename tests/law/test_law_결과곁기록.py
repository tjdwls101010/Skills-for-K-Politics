"""`수집.py --결과` 가 남기는 곁기록 — **워크플로 요약이 읽는 유일한 구조화 출력이다.**

⚠️ **곁기록이 없는 종료 경로가 하나라도 있으면 그날 요약은 아무 말도 못 한다.** 그리고
   그런 날은 대개 뭔가 잘못된 날이다 — 인증이 깨졌거나, 원천이 흔들렸거나, 먼저 도는
   실행에 막혔거나. 잘 끝난 날에만 남는 기록은 있으나 마나다.

⚠️ **종료코드만으로 문구를 정하면 안 된다.** 인증 실패도 `1` 인데 그때 감사는 아예
   안 돌았다 — `1` 을 "게이트 위반, 사람이 봐야 한다"로 읽으면 요약이 거짓말을 한다.
   `종류` 가 그 구별을 지고, 여기가 그 계약을 잰다.
"""

from __future__ import annotations

import json

import pytest

from 법제처 import 감사
실행 = pytest.importorskip("법제처.수집기.실행")
연결 = pytest.importorskip("법제처.연결")
원천 = pytest.importorskip("법제처.원천")
이행 = pytest.importorskip("법제처.이행")




@pytest.fixture
def 채운DB(db_path):
    conn = 연결.connect(db_path)
    이행.init_schema(conn)
    conn.close()
    return db_path


def _돌린다(채운DB, tmp_path, *인자):
    곁 = tmp_path / "result.json"
    코드 = 실행._main(["--db", str(채운DB), "--결과", str(곁), *인자])
    assert 곁.exists(), f"곁기록이 안 남았다 (종료코드 {코드})"
    return 코드, json.loads(곁.read_text())


class 못잡는락:
    잡음 = False

    def __init__(self, *a, **kw):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_네트워크를_안_타는_실행도_곁기록을_남긴다(채운DB, tmp_path) -> None:
    """`--재파싱` 은 DB 안의 원문만 다시 읽는다. 그래도 감사는 돌고, 요약은 그날도 나온다."""
    코드, r = _돌린다(채운DB, tmp_path, "--재파싱")
    assert r["코퍼스"] == "law" and r["판정"] == 코드
    assert r["감사실행"] is True and r["게이트"], "감사가 돌았는데 게이트가 비었다"
    assert any(단["이름"] == "파싱" for 단 in r["단계"])
    assert r["소요초"] >= 0


def test_게이트가_전부_통과하면_정상으로_남는다(채운DB, tmp_path, monkeypatch) -> None:
    """빈 DB 는 늘 빨가므로(A1b·A24) 초록 갈래는 게이트를 갈아 끼워야 볼 수 있다.
    **초록도 곁기록에 남아야 한다** — 요약이 "오늘 정상"이라고 말할 근거가 이것뿐이다."""


    monkeypatch.setattr(감사, "게이트", [("A99", "언제나 0", "SELECT 0")])
    monkeypatch.setattr(감사, "보고", [])
    코드, r = _돌린다(채운DB, tmp_path, "--감사만")
    assert 코드 == 0
    assert r["종류"] == "정상" and r["판정"] == 0
    assert [g["번호"] for g in r["게이트"]] == ["A99"]
    assert r["게이트"][0]["ok"] is True and r["게이트"][0]["해석"] is None


def test_빨간_게이트는_해석까지_실려_나간다(채운DB, tmp_path) -> None:
    """⚠️ **해석은 지금 stdout 에만 있다.** 요약 표에 못 실으면 사람은 게이트 이름
    ("무엇을 셌나")만 보고 "그래서 무슨 일이 났나"는 로그를 뒤져야 한다."""
    코드, r = _돌린다(채운DB, tmp_path, "--감사만")
    assert 코드 == 1, "빈 DB 는 A1b 로 빨개야 한다"
    assert r["종류"] == "게이트" and r["판정"] == 1
    빨강 = [g for g in r["게이트"] if not g["ok"]]
    assert 빨강 and all(g["번호"] and g["이름"] for g in 빨강)
    assert any(g["해석"] for g in 빨강), "해석이 붙는 게이트가 하나도 안 실렸다"
    assert r["보고"], "보고값이 없으면 빨간불 앞에서 할 수 있는 일이 없다"


def test_락에_막히면_성공과_구별되는_표지가_남는다(채운DB, tmp_path, monkeypatch) -> None:
    """⚠️ **물러난 것은 실패가 아니지만 성공과 같은 얼굴이어서도 안 된다.** 국회와 같이
    3 을 낸다 — 워크플로가 경고 주석을 단 채 초록으로 끝내, 실행 목록에서 "오늘 아무것도
    안 받은 날"이 구별된다. 요약의 문구는 `종류` 가 소유한다."""
    monkeypatch.setattr(연결, "락", 못잡는락)
    코드, r = _돌린다(채운DB, tmp_path)
    assert 코드 == 3
    assert r["종류"] == "락"
    assert r["감사실행"] is False, "감사가 안 돌았는데 돌았다고 적혔다"


@pytest.mark.parametrize(
    ("터뜨릴것", "종류", "코드"),
    [(원천.인증실패, "인증", 1), (원천.API오류, "중단", 2)],
)
def test_중간에_죽어도_왜_죽었는지가_남는다(채운DB, tmp_path, monkeypatch,
                                터뜨릴것, 종류, 코드) -> None:
    """⚠️ **인증 실패와 게이트 위반은 둘 다 1 이다.** 감사가 돌았는지가 그 둘을 가르고,
    그 사실은 곁기록 말고 어디에도 안 남는다 — 종료코드는 프로세스와 함께 사라진다."""
    def 터진다(*a, **kw):
        raise 터뜨릴것("가짜")

    monkeypatch.setattr(원천, "Client", 터진다)
    난코드, r = _돌린다(채운DB, tmp_path)
    assert 난코드 == 코드
    assert r["종류"] == 종류
    assert r["감사실행"] is False


def test_곁기록을_안_시키면_안_만든다(채운DB, tmp_path, monkeypatch) -> None:
    """대화형 실행에 파일을 흘리지 않는다 — 곁기록은 워크플로가 시킬 때만 남는다.

    ⚠️ **작업 디렉터리를 옮겨 놓고 센다.** `tmp_path` 만 보면 구현이 실수로 **현재
    디렉터리에** `result.json` 을 떨어뜨려도 검사가 통과한다.
    """
    monkeypatch.chdir(tmp_path)
    실행._main(["--db", str(채운DB), "--재파싱"])
    assert not list(tmp_path.glob("*.json"))


def test_감사는_실행당_한_번만_돈다(채운DB, tmp_path, monkeypatch) -> None:
    """⚠️ **게이트 하나가 30만 행을 훑는다.** 두 번 도는 것은 자동 수집의 꼬리를 몇 분
    늘리는 일이고, 그 사이 DB 가 바뀌면 **화면에 낸 표와 곁기록의 값이 갈린다.**
    셋(화면·곁기록·`메타`)이 각자 `run` 을 부르던 모양으로 되돌아가는 것을 막는다."""


    센다 = []
    원래 = 감사.run
    monkeypatch.setattr(감사, "run", lambda conn: (센다.append(1), 원래(conn))[1])
    _돌린다(채운DB, tmp_path, "--재파싱")
    assert len(센다) == 1, f"감사가 {len(센다)}번 돌았다"
