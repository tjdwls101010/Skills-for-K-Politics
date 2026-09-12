"""조회의 출발점인 뷰가 오늘의 답을 주는가.

⚠️ **이 파일이 지키는 것은 하나다 — 뷰를 고치다 오늘 효력인 법을 지워 버리지 않는 것.**
`현행법령` 에 `시행일자 <= 오늘` 을 거는 것은 자연스러워 보이는데, 그러면 형법이 통째로
0건이 된다. 원천이 개정을 여러 날에 걸쳐 나누어 시행할 때 **목록은 처음 시행일을, 본문은
마지막을** 주고 우리가 담는 것은 본문 쪽이기 때문이다. 0건은 "그런 법이 없다"로 읽힌다.
"""

from __future__ import annotations

import sqlite3

import pytest

from 법제처 import 이행

# ⚠️ **날짜 리터럴을 미래로 가정하지 마라.** 형법의 실제 시행일 2026-09-13 을 박으면
#    그날부터 이 파일이 **코드를 한 줄도 안 고쳤는데** 빨개진다. 뷰가 `date('now')` 를
#    보므로 테스트도 같은 시계를 봐야 한다.
_c = sqlite3.connect(":memory:")
미래 = _c.execute("SELECT date('now','localtime','+30 days')").fetchone()[0]
과거 = _c.execute("SELECT date('now','localtime','-30 days')").fetchone()[0]
_c.close()


@pytest.fixture
def 형법(conn):
    """실측에서 그대로 가져온 형법 22대 개정판. **꾸며 낸 상황이 아니다.**

    MST 284025 · 공포 2026-03-12 · 본문 시행일자 2026-09-13. 원천 목록은 같은 MST 를
    2026-03-12 시행으로 준다 — 즉 **오늘 효력인 법인데 본문은 미래 렌더링**이다.
    그 본문에는 제98조(적국을 위한 간첩)와 제98조의2(외국 등을 위한 간첩)가 함께 들어 있고,
    둘 다 `조문변경여부='Y'` 로만 표시된다.
    """
    conn.execute(
        "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,공포일자,공포번호,"
        "시행일자,제개정구분,현행연혁코드,수집일시) VALUES "
        "('284025','001692','형법','법률','2026-03-12','21450',?,"
        "'일부개정','현행','2026-08-28 00:00:00')", (미래,))
    for 순서, (키, 번호, 가지, 제목, 변경) in enumerate([
        ("0098001", 98, 0, "적국을 위한 간첩", "Y"),
        ("0098021", 98, 2, "외국 등을 위한 간첩", "Y"),
        ("0123001", 123, 0, "직권남용", "N"),
    ]):
        conn.execute(
            "INSERT INTO 조문 (법령일련번호,조문키,조문번호,조문가지번호,조문제목,"
            "조문내용,전문,조문시행일자,조문변경여부,조문여부,순서) VALUES "
            "('284025',?,?,?,?,?,?,?,?,'조문',?)",
            (키, 번호, 가지, 제목, f"제{번호}조({제목})", f"제{번호}조({제목}) 본문",
             미래, 변경, 순서))
    return conn


class Test현행법령:
    def test_본문시행일이_미래여도_오늘의_현행법에서_빠지지_않는다(self, 형법):
        """**이 스테이지가 막으려는 오답이 바로 이것이다.**"""
        assert 형법.execute(
            "SELECT COUNT(*) FROM 현행법령 WHERE 법령명='형법'").fetchone()[0] == 1

    def test_본문이_미래_렌더링이면_시행대기로_표시된다(self, 형법):
        행 = 형법.execute("SELECT * FROM 현행법령 WHERE 법령명='형법'").fetchone()
        assert 행["시행대기"] == 1

    def test_시행일이_지난_현행은_시행대기가_아니다(self, conn):
        conn.execute(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES "
            "('1','L1','민법','법률',?,'현행','2026-08-28 00:00:00')", (과거,))
        assert conn.execute(
            "SELECT 시행대기 FROM 현행법령 WHERE 법령명='민법'").fetchone()[0] == 0


class Test시행대기법령:
    def test_본문이_미래_렌더링인_법령을_짚어_준다(self, 형법):
        """조문을 인용하기 전에 부칙을 읽어야 하는 법이 무엇인지 한 질의로 나와야 한다."""
        assert [r[0] for r in 형법.execute(
            "SELECT 법령명 FROM 시행대기법령")] == ["형법"]

    def test_만료없음_표식은_시행대기가_아니다(self, conn):
        """⚠ 원천은 만료 없음을 '9999-01-01' 로 준다. 그걸 시행대기로 세면
        **영원히 빨간 목록**이 되고, 그러면 아무도 안 본다."""
        conn.execute(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES "
            "('2','L2','만료없음법','법률','9999-01-01','현행','2026-08-28 00:00:00')")
        assert conn.execute("SELECT COUNT(*) FROM 시행대기법령").fetchone()[0] == 0
        # ⚠️ **표지와 목록이 갈리면 아무도 안 본다.** 표지는 1 인데 목록에는 없는 법이
        #    생기면, 그 법을 확인하려는 사람이 목록에서 못 찾고 없는 것으로 읽는다.
        assert conn.execute(
            "SELECT 시행대기 FROM 현행법령 WHERE 법령명='만료없음법'").fetchone()[0] == 0

    def test_표지와_목록이_언제나_같은_집합이다(self, 형법):
        형법.executemany(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES (?,?,?,'법률',?,'현행','2026-08-28 00:00:00')",
            [("2", "L2", "만료없음법", "9999-01-01"),
             ("3", "L3", "날짜없는법", None),
             ("4", "L4", "시행중인법", 과거)])
        표지 = {r[0] for r in 형법.execute(
            "SELECT 법령명 FROM 현행법령 WHERE 시행대기=1")}
        목록 = {r[0] for r in 형법.execute("SELECT 법령명 FROM 시행대기법령")}
        assert 표지 == 목록 == {"형법", "날짜없는법"}

    def test_시행일자를_모르면_확인하라고_말한다(self, conn):
        """⚠ **NULL 은 `=0` 과 `=1` 양쪽에서 사라진다.** 그러면 `WHERE 시행대기=0` 으로
        "오늘의 법"을 뽑는 조회에서 조용히 빠지고, 확인 목록에도 안 뜬다 — 양쪽에서
        안 보이는 것이 가장 나쁘다. 모르는 것은 **확인 대상**으로 올린다."""
        conn.execute(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES "
            "('5','L5','날짜없는법','법률',NULL,'현행','2026-08-28 00:00:00')")
        assert conn.execute(
            "SELECT 시행대기 FROM 현행법령 WHERE 법령명='날짜없는법'").fetchone()[0] == 1

    def test_시행일자를_모르는_시행예정도_빠지지_않는다(self, conn):
        conn.execute(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES "
            "('6','L6','날짜없는예정법','법률',NULL,'시행예정','2026-08-28 00:00:00')")
        assert conn.execute("SELECT COUNT(*) FROM 시행예정법령").fetchone()[0] == 1


class Test시행예정법령:
    def test_시행일이_이미_지난_옛_시행예정은_빠진다(self, conn):
        """⚠ 시행 전에 다시 개정돼 밀려난 버전이 `시행예정` 코드를 단 채 남는다.
        그것까지 세면 "앞으로 무엇이 시행되나"의 답이 조용히 넓어진다."""
        conn.executemany(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES (?,?,?,'법률',?,'시행예정','2026-08-28 00:00:00')",
            [("10", "L3", "밀려난법", 과거), ("11", "L4", "진짜예정법", 미래)])
        assert [r[0] for r in conn.execute(
            "SELECT 법령명 FROM 시행예정법령")] == ["진짜예정법"]


class Test현행조문:
    def test_조문에도_시행대기가_따라온다(self, 형법):
        """조문만 뽑아 인용하는 것이 가장 흔한 경로다. 표지가 법령 뷰에만 있으면
        **바로 그 경로에서 안 보인다.**"""
        행들 = 형법.execute(
            "SELECT 조문제목, 시행대기, 조문변경여부 FROM 현행조문 "
            "WHERE 법령명='형법' AND 조문번호=98 ORDER BY 순서").fetchall()
        assert [r["조문제목"] for r in 행들] == ["적국을 위한 간첩", "외국 등을 위한 간첩"]
        assert all(r["시행대기"] == 1 for r in 행들)
        # 어느 조가 이번 개정에 손댄 것인지는 이 컬럼이 유일한 단서다.
        assert all(r["조문변경여부"] == "Y" for r in 행들)


class Test의율판단:
    """⚠ **`자료ID` 는 네 자료종류를 가리키는 다형 키다.** 종류를 넘나들어 값이 겹치는데,
    `COUNT(DISTINCT 자료ID)` 로 세면 겹친 둘이 하나로 접혀 **에러 없이 과소 집계된다.**
    """

    @pytest.fixture
    def 의율(self, conn):
        conn.executemany(
            "INSERT INTO 의율조문 (자료종류,자료ID,출처필드,쟁점번호,법령명원문,법령ID,"
            "조,항,원문조각) VALUES (?,?,'참조조문',?,'민법','001',750,?,?)",
            [   # 같은 판단이 같은 조를 쟁점 1·2 에서 두 번 가리킨다 — 접혀야 한다
                ("판례", "100", 1, None, "민법 제750조"),
                ("판례", "100", 2, None, "민법 제750조"),
                # 같은 판단이 같은 조의 다른 **항** 을 가리킨다 — 조 랭킹에서는 한 건이다
                ("판례", "100", None, 1, "민법 제750조제1항"),
                ("판례", "100", None, 3, "민법 제750조제3항"),
                # 자료ID 가 종류를 넘나들어 충돌한다 — 갈라져야 한다
                ("헌재결정례", "100", None, None, "민법 제750조"),
            ])
        return conn

    def test_같은_법을_다르게_적어도_한_건이다(self, conn):
        """⚠ **원천 표기가 띄어쓰기·약칭으로 흔들린다.** 법령ID 를 이미 푼 행에서
        이름까지 좌표에 넣으면 같은 판단이 표기 수만큼 세진다."""
        conn.executemany(
            "INSERT INTO 의율조문 (자료종류,자료ID,출처필드,법령명원문,법령ID,조,원문조각)"
            " VALUES ('판례','200','참조조문',?,'001',750,?)",
            [("민법", "민법 제750조"), ("민 법", "민 법 제750조")])
        assert conn.execute("SELECT COUNT(*) FROM 의율판단").fetchone()[0] == 1

    def test_법령ID_를_못_풀었으면_이름이_좌표다(self, conn):
        """구법·약칭 표기는 매칭이 실패해 법령ID 가 NULL 이다. 그때까지 이름을 버리면
        **서로 다른 법이 한 건으로 합쳐진다.**"""
        conn.executemany(
            "INSERT INTO 의율조문 (자료종류,자료ID,출처필드,법령명원문,법령ID,조,원문조각)"
            " VALUES ('판례','300','참조조문',?,NULL,750,?)",
            [("구 민법", "구 민법 제750조"), ("구 상법", "구 상법 제750조")])
        assert conn.execute(
            "SELECT COUNT(*) FROM 의율판단 WHERE 자료ID='300'").fetchone()[0] == 2

    def test_한_판단이_같은_조의_여러_항을_가리켜도_한_건이다(self, 의율):
        """⚠️ **단위는 판단 × 조다.** 항·호를 남기면 "가장 많이 다퉈진 조" 랭킹이
        항을 여럿 인용한 판단만큼 부푼다 — 실측으로 형법 제355조가 4% 높게 나온다."""
        assert 의율.execute(
            "SELECT COUNT(*) FROM 의율판단 WHERE 자료종류='판례' AND 조=750"
        ).fetchone()[0] == 1

    def test_한_판단이_쟁점별로_여러_번_가리켜도_한_건이다(self, 의율):
        assert 의율.execute(
            "SELECT COUNT(*) FROM 의율판단 WHERE 자료종류='판례'").fetchone()[0] == 1

    def test_자료ID_가_충돌해도_다른_판단으로_센다(self, 의율):
        """`COUNT(DISTINCT 자료ID)` 는 여기서 1 을 준다 — 그것이 지금의 오답이다."""
        assert 의율.execute("SELECT COUNT(*) FROM 의율판단").fetchone()[0] == 2
        assert 의율.execute(
            "SELECT COUNT(DISTINCT 자료ID) FROM 의율조문").fetchone()[0] == 1


class Test뷰갱신:
    """⚠️ **이미 있는 DB 에 새 정의가 실제로 들어가는가.** 뷰는 값이 없어서 눈에 안 띄는데,
    `CREATE VIEW IF NOT EXISTS` 였다면 **기존 DB 안에서 옛 정의가 그대로 살아남는다** —
    코드와 테스트는 새 정의를 보고 초록인데 의원실이 조회하는 DB 만 낡는다.
    """

    def test_옛_정의가_남아_있어도_갈아_끼운다(self, conn):
        conn.execute("DROP VIEW 현행법령")
        conn.execute("CREATE VIEW 현행법령 AS SELECT 법령명 FROM 법령 WHERE 0")
        이행.init_schema(conn)
        conn.execute(
            "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
            "현행연혁코드,수집일시) VALUES "
            "('9','L9','민법','법률',?,'현행','2026-08-28 00:00:00')", (과거,))
        assert conn.execute(
            "SELECT 시행대기 FROM 현행법령 WHERE 법령명='민법'").fetchone()[0] == 0

    def test_기존_행을_건드리지_않는다(self, 형법):
        형법.execute("INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,"
                    "현행연혁코드,수집일시) VALUES "
                    "('8','L8','민법','법률',?,'현행','2026-08-28 00:00:00')", (과거,))
        전 = 형법.execute("SELECT COUNT(*) FROM 법령").fetchone()[0]
        이행.init_schema(형법)
        assert 형법.execute("SELECT COUNT(*) FROM 법령").fetchone()[0] == 전
        assert 형법.execute("PRAGMA quick_check").fetchone()[0] == "ok"
