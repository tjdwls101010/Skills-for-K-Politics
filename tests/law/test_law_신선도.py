"""`신선도` 뷰 — "이 답은 언제 기준인가"가 한 질의로 끝난다.

컬럼은 국회 쪽과 같다. 스킬이 두 DB 에 같은 질의를 주입하므로 모양이 갈리면 주입 줄도 갈린다.
빈 DB 에서도 한 행이어야 하는 이유는 `tests/congress/test_신선도.py` 에 적혀 있다.
"""


def 신선도(conn):
    행들 = conn.execute("SELECT * FROM 신선도").fetchall()
    assert len(행들) == 1, f"{len(행들)}행이다 — 신선도는 항상 한 행이다"
    return 행들[0]


def test_메타가_비어_있으면_한_행이고_값은_전부_NULL(conn):
    assert tuple(신선도(conn)) == (None, None, None, None, None)


def _메타(conn, **키값):
    conn.executemany("INSERT INTO 메타 (키,값) VALUES (?,?)", list(키값.items()))


def test_통과한_감사는_감사통과_1_이고_상세가_없다(conn):
    _메타(conn, 마지막수집일시="2026-09-12 08:21:44", 마지막감사="통과", 마지막감사일시="2026-09-12 08:25:10")
    행 = 신선도(conn)
    assert 행["적재기준시각"] == "2026-09-12 08:21:44"
    assert 행["감사통과"] == 1
    assert 행["감사시각"] == "2026-09-12 08:25:10"
    assert 행["감사상세"] is None


def test_위반_문자열은_감사통과_0_과_그_문자열_그대로를_준다(conn):
    _메타(conn, 마지막감사="A1=3", 마지막감사일시="2026-09-11 08:25:10")
    행 = 신선도(conn)
    assert 행["감사통과"] == 0
    assert 행["감사상세"] == "A1=3"


def test_경고는_이_코퍼스에_없다(conn):
    _메타(conn, 마지막감사="통과")
    assert 신선도(conn)["경고"] is None
