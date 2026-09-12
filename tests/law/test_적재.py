"""`적재.py` — 스키마 · 연결 · 쓰기 경로 · 마이그레이션."""

import re
import sqlite3

import pytest

from 법제처 import 감사
from 법제처 import 스키마
from 법제처 import 연결
from 법제처 import 이행
from 법제처 import 저장
from 법제처 import 정규화


class Test스키마:
    def test_두_번_적용해도_같다(self, conn):
        이행.init_schema(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        assert n == 18  # 위임 추가 · 행정규칙별표 삭제(D42) · 정리후보 추가

    def test_모든_CREATE_TABLE_을_정규식이_잡는다(self):
        """⚠️ **`_테이블문` 이 못 잡은 테이블은 `migrate()` 도 `_테이블재구축` 도 못 본다.**

        정규식이 닫는 괄호를 줄 맨 앞(`^\\);`)에서 찾으므로 **한 줄로 쓴 `CREATE TABLE` 이
        통째로 빠진다.** 빠져도 아무 신호가 없다 — 그 테이블의 주석만 DB 안에서 영원히
        낡고, 재구축 대상으로 넘겨도 조용히 건너뛴다.
        """
        잡힌것 = {m.group(1) for m in 스키마._테이블문.finditer(스키마.SCHEMA)}
        전부 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\S+)", 스키마.SCHEMA))
        assert 잡힌것 == 전부, f"정규식이 놓친 테이블: {sorted(전부 - 잡힌것)}"

    def test_스키마에는_PRAGMA_가_없다(self):
        # 연결 속성이라 connect() 가 매번 다시 걸어야 한다. 두 곳에 적으면 한쪽만 고치게 된다.
        assert "PRAGMA" not in 스키마.SCHEMA


class TestCHECK:
    """**`CHECK` 은 낡을 수 없는 주석이다 — 낡으면 `INSERT` 가 실패한다.**

    이 DB 에서 오늘까지 값 집합은 주석에만 있었고, 실측으로 낡은 주석을 여섯 곳 찾았다.
    우리 설계가 집합을 닫은 넷에만 건다(D41) — 원천이 값을 늘릴 수 있는 `출처필드`·
    `수집상태.단계`·`실패종류`·`파싱실패.사유` 에는 안 건다. 그건 레일이 된다.
    """

    @pytest.mark.parametrize(
        "무엇, sql",
        [("의율조문.자료종류",
          "INSERT INTO 의율조문 (자료종류, 자료ID, 출처필드, 법령명원문, 원문조각)"
          " VALUES ('위원회결정', '1', '참조조문', '개인정보 보호법', 'x')"),
         # 인용판례는 둘뿐이다 — 판례·헌재만 참조판례를 준다
         ("인용판례.자료종류",
          "INSERT INTO 인용판례 (자료종류, 자료ID, 피인용사건번호, 원문조각)"
          " VALUES ('행정심판례', '1', '2018다244488', 'x')"),
         ("법령.현행연혁코드",
          "INSERT INTO 법령 (법령일련번호, 법령ID, 법령명, 법종구분, 현행연혁코드, 수집일시)"
          " VALUES ('270351', '011357', '개인정보 보호법', '법률', '연혁', 'x')"),
         ("조문.조문여부",
          "INSERT INTO 조문 (법령일련번호, 조문키, 조문번호, 조문내용, 전문, 조문여부, 순서)"
          " VALUES ('270351', '0015001', 15, 'x', 'x', '본문', 0)")],
    )
    def test_닫아_둔_값집합_밖은_INSERT_가_막는다(self, conn, 무엇, sql):
        if 무엇 == "조문.조문여부":  # FK 가 먼저 걸리지 않게 부모를 둔다
            저장.저장(conn, "법령", _법령())
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(sql)

    def test_옳은_값은_그대로_들어간다(self, conn):
        """⚠️ 막는 것만 시험하면 **전부 막는 CHECK** 도 통과한다."""
        저장.저장(conn, "법령", _법령())
        저장.저장(conn, "법령", _법령("999999", 연혁="시행예정"))
        for 종류 in ("판례", "헌재결정례", "행정심판례", "법령해석례"):
            conn.execute(
                "INSERT INTO 의율조문 (자료종류, 자료ID, 출처필드, 법령명원문, 원문조각)"
                " VALUES (?, '1', '참조조문', '개인정보 보호법', 'x')", (종류,)
            )
        assert conn.execute("SELECT COUNT(*) FROM 의율조문").fetchone()[0] == 4


class Test뷰:
    """**뷰는 산문이 지키던 함정을 인터페이스로 옮긴 것이다**(D40).

    `.schema` 안에 살아 매 세션 공짜로 다시 읽히고, 함정표 한 줄은 읽고 기억해야 한다.
    이름이 `현행-` 으로 시작하는 것이 요점이다 — `조문` 을 직접 쓰면 시행예정과 장 제목이
    섞인다는 사실이 **이름에 적혀 있게** 된다.
    """

    @pytest.fixture
    def 섞인DB(self, conn):
        """현행 1 + 시행예정 1, 각각 조문 1행 + 편·장 제목 1행. **실물의 축소판이다.**

        실측: `조문.전문 LIKE '%광고성 정보%'` 를 필터 없이 세면 55, 두 조건을 걸면 24 다.
        """
        저장.저장(conn, "법령", _법령("270351", 연혁="현행"),
                {"조문": [_조문("0015001", 15, "제15조 현행 …", 1),
                          {**_조문("0015000", 15, "제3장 개인정보의 처리", 0),
                           "조문여부": "전문"}]})
        저장.저장(conn, "법령", _법령("283839", 연혁="시행예정"),
                {"조문": [_조문("0015001", 15, "제15조 시행예정 …", 1)]})
        return conn

    def test_현행법령_이_시행예정을_뺀다(self, 섞인DB):
        assert [r[0] for r in 섞인DB.execute("SELECT 법령일련번호 FROM 현행법령")] == ["270351"]

    def test_현행조문_이_시행예정과_장제목을_한꺼번에_뺀다(self, 섞인DB):
        """⚠️ **함정 둘을 뷰 하나가 없앤다.** `법령ID` 만 걸면 시행예정 조문이 섞이고,
        `조문번호=15` 만 걸면 편·장 제목 행이 함께 나온다 — 그 행도 조문번호를 갖고
        실제 조와 겹치기 때문이다. 둘 다 에러 없이 그럴듯한 답을 낸다."""
        섞인 = 섞인DB.execute(
            "SELECT COUNT(*) FROM 조문 j JOIN 법령 l USING (법령일련번호)"
            " WHERE l.법령ID='011357' AND j.조문번호=15").fetchone()[0]
        뷰 = 섞인DB.execute(
            "SELECT 전문 FROM 현행조문 WHERE 법령ID='011357' AND 조문번호=15").fetchall()
        assert 섞인 == 3
        assert [r[0] for r in 뷰] == ["제15조 현행 …"]

    def test_현행조문_이_목차_조회에_그대로_쓰인다(self, 섞인DB):
        """3단계 ②(목차)와 전문 가로지르기가 뷰 하나로 끝나야 한다 — 두 질의가 서로 다른
        테이블을 보면 ②를 건너뛰는 쪽으로 손이 간다."""
        목차 = 섞인DB.execute(
            "SELECT 조문번호, 조문가지번호, 조문제목 FROM 현행조문"
            " WHERE 법령ID='011357' ORDER BY 순서").fetchall()
        assert len(목차) == 1

    def test_두_시행일자가_이름으로_갈린다(self, 섞인DB):
        """⚠️ `법령.시행일자` 와 `조문.조문시행일자` 는 **다를 수 있다** — 개정법이 일부
        조문만 유예하는 것은 통상이다. 뷰가 둘을 같은 이름으로 내놓으면 그 구분이 사라진다."""
        컬럼 = [r[1] for r in 섞인DB.execute("PRAGMA table_info(현행조문)")]
        assert "법령시행일자" in 컬럼 and "조문시행일자" in 컬럼
        assert len(컬럼) == len(set(컬럼))

    def test_조문내용은_뷰에_없다(self, 섞인DB):
        """**틀린 손을 막는 가장 싼 방법은 그 컬럼을 안 내놓는 것이다.**

        항이 있는 조에서 `조문내용` 은 제목 한 줄뿐이라 검색에 쓰면 본문의 93%가 조용히
        사라진다. 지금까지 그건 문서의 함정표 한 줄이 지키고 있었는데, 함정표는 읽고
        기억해야 하고 뷰의 컬럼 목록은 `.schema` 에 적혀 있다.
        """
        컬럼 = [r[1] for r in 섞인DB.execute("PRAGMA table_info(현행조문)")]
        assert "조문내용" not in 컬럼
        assert "전문" in 컬럼

    def test_뷰_정의를_고치면_기존_DB_에도_반영된다(self, conn):
        """⚠️ **`CREATE VIEW IF NOT EXISTS` 는 정의가 바뀌어도 갱신하지 않는다.**
        그대로 두면 뷰가 이 DB 안에서 낡고, `.schema` 를 읽는 클로드는 낡은 정의를
        정본으로 읽는다 — 테이블 주석에 `migrate()` 가 필요한 것과 같은 이유다."""
        conn.execute("DROP VIEW 현행법령")
        conn.execute("CREATE VIEW 현행법령 AS SELECT * FROM 법령")  # 낡은 정의
        이행.init_schema(conn)
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name='현행법령'"
        ).fetchone()[0]
        assert "현행연혁코드='현행'" in 저장된


class Test연결:
    def test_새_연결마다_외래키가_켜진다(self, db_path):
        """⚠️ `foreign_keys` 는 연결 속성이고 기본값이 OFF 다. 이 함수를 거치지 않는
        경로가 하나라도 있으면 스키마의 FK 전부가 장식이 된다 — 아무 증상도 없이."""
        c = 연결.connect(db_path)
        이행.init_schema(c)
        c.close()
        c2 = 연결.connect(db_path)
        assert c2.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c2.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        c2.close()

    def test_FK_가_실제로_막는다(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO 조문 (법령일련번호, 조문키, 조문번호, 조문내용, 전문, 조문여부, 순서)"
                " VALUES ('없는법령', '0001001', 1, 'x', 'x', '조문', 0)"
            )

    def test_db_경로는_환경변수가_기본값을_이긴다(self, tmp_path, monkeypatch):
        # GitHub Actions 가 DB 를 체크아웃 밖으로 보내는 유일한 수단이다.
        monkeypatch.setenv("LAW_DB", str(tmp_path / "환경.db"))
        assert 연결.db_path() == tmp_path / "환경.db"
        assert 연결.db_path(tmp_path / "명시.db") == tmp_path / "명시.db"

    def test_환경변수가_없으면_스킬_디렉터리다(self, monkeypatch):
        monkeypatch.delenv("LAW_DB", raising=False)
        assert 연결.db_path().name == "법령.db"
        assert 연결.db_path().parent.name == "원천"


def _법령(일련="270351", 명="개인정보 보호법", 연혁="현행"):
    return {
        "법령일련번호": 일련,
        "법령ID": "011357",
        "법령명": 명,
        "법종구분": "법률",
        "현행연혁코드": 연혁,
    }


def _조문(키="0015001", 번호=15, 전문="제15조 …", 순서=0):
    return {
        "조문키": 키,
        "순서": 순서,
        "조문번호": 번호,
        "조문가지번호": 0,
        "조문내용": "제15조(개인정보의 수집ㆍ이용)",
        "전문": 전문,
        "조문여부": "조문",
    }


class Test저장:
    def test_본문과_자식이_함께_들어간다(self, conn):
        저장.저장(
            conn,
            "법령",
            _법령(),
            {
                "조문": [_조문()],
                "조문요소": [
                    {"조문순서": 0, "항번호": 1, "깊이": 1, "내용": "① …", "순서": 0}
                ],
                "부칙": [{"순서": 0, "내용": "부칙 …"}],
            },
        )
        assert conn.execute("SELECT COUNT(*) FROM 법령").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM 조문").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM 조문요소").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM 부칙").fetchone()[0] == 1

    def test_수집일시가_자동으로_채워진다(self, conn):
        저장.저장(conn, "법령", _법령())
        assert conn.execute("SELECT 수집일시 FROM 법령").fetchone()[0]

    def test_다시_저장하면_옛_자식이_남지_않는다(self, conn):
        """⚠️ 개정으로 조문이 통째로 갈릴 수 있다. 부분 갱신은 옛 조문을 남기고,
        그러면 없는 조문이 검색에 계속 걸린다."""
        저장.저장(conn, "법령", _법령(), {"조문": [_조문("0015001", 15), _조문("0016001", 16, 순서=1)]})
        저장.저장(conn, "법령", _법령(), {"조문": [_조문("0015001", 15, "개정된 제15조")]})
        행 = conn.execute("SELECT 조문키, 전문 FROM 조문").fetchall()
        assert [r[0] for r in 행] == ["0015001"]
        assert 행[0][1] == "개정된 제15조"

    def test_같은_법령ID_의_다른_버전은_공존한다(self, conn):
        # 현행 1 + 시행예정 N. 조문 조회에서 현행연혁코드를 안 걸면 섞여 나온다(07 §1).
        저장.저장(conn, "법령", _법령("270351", 연혁="현행"))
        저장.저장(conn, "법령", _법령("999999", 연혁="시행예정"))
        assert conn.execute(
            "SELECT COUNT(*) FROM 법령 WHERE 법령ID='011357'"
        ).fetchone()[0] == 2

    def test_저장하면_수집실패에서_지워진다(self, conn):
        저장.record_failure(conn, "법령", "270351", "네트워크", "타임아웃")
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 1
        저장.저장(conn, "법령", _법령())
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 0

    def test_스키마에_없는_키는_조용히_버린다(self, conn):
        # 원천이 필드를 더 주는 일이 잦다(조문이동이전 등). 그때마다 터지면 안 된다.
        저장.저장(conn, "법령", {**_법령(), "원천이준_모르는필드": "x"})
        assert conn.execute("SELECT COUNT(*) FROM 법령").fetchone()[0] == 1

    def test_쓸_컬럼이_하나도_없으면_터진다(self, conn):
        # 조용히 빈 행을 만드는 것보다 알아볼 수 있게 터지는 쪽이 낫다.
        with pytest.raises(ValueError, match="쓸 컬럼"):
            저장._삽입(conn, "판례", {"전부": "모르는", "필드": "다"})

    def test_없는_테이블은_알아볼_수_있게_터진다(self, conn):
        with pytest.raises(LookupError, match="그런 테이블이 없다"):
            저장.컬럼들(conn, "없는테이블")

    def test_기본키가_비면_터진다(self, conn):
        with pytest.raises(ValueError, match="법령일련번호"):
            저장.저장(conn, "법령", {**_법령(), "법령일련번호": ""})

    def test_여섯_종_전부_저장_경로가_있다(self, conn):
        assert set(저장.본문테이블) == {
            "법령", "판례", "헌재결정례", "행정심판례", "법령해석례", "행정규칙",
        }
        저장.저장(conn, "판례", {"판례일련번호": "1", "사건번호": "2026도477"})
        저장.저장(conn, "헌재결정례", {"헌재결정례일련번호": "1", "사건번호": "2015헌마1140"})
        저장.저장(conn, "행정심판례", {"행정심판례일련번호": "1", "사건번호": "2022 경기행심 1680"})
        저장.저장(conn, "법령해석례", {"법령해석례일련번호": "1", "안건명": "…"})
        저장.저장(
            conn,
            "행정규칙",
            {"행정규칙일련번호": "1", "행정규칙ID": "97361", "행정규칙명": "…"},
            {"행정규칙조문": [{"순서": 0, "내용": "제1조(목적) …"}]},
        )
        assert conn.execute("SELECT COUNT(*) FROM 행정규칙조문").fetchone()[0] == 1


class Test트랜잭션:
    """⚠️ `connect()` 가 `isolation_level=None`(autocommit)로 열기 때문에 **`with conn:` 은
    트랜잭션을 시작하지 않는다.** 실제로 그렇게 써 놨었고, 블록 중간에 터져도 이미 쓴 것이
    그대로 남았다. 반쪽 레코드가 영구 커밋되고 다음 실행이 그걸 완성된 것으로 본다."""

    def test_예외가_나면_되돌린다(self, conn):
        with pytest.raises(RuntimeError):
            with 연결.트랜잭션(conn):
                conn.execute(
                    "INSERT INTO 메타 (키, 값) VALUES ('시험', '1')"
                    " ON CONFLICT(키) DO UPDATE SET 값='1'"
                )
                raise RuntimeError("중간에 터진다")
        assert 저장.메타읽기(conn, "시험") is None

    def test_with_conn_은_되돌리지_않는다(self, conn):
        # 이 테스트는 **왜 트랜잭션() 이 필요한지**를 잠근다. 여기가 깨지면
        # 파이썬 sqlite3 의 동작이 바뀐 것이므로 트랜잭션() 을 다시 검토해야 한다.
        with pytest.raises(RuntimeError):
            with conn:
                conn.execute("INSERT INTO 메타 (키, 값) VALUES ('시험2', '1')")
                raise RuntimeError("x")
        assert 저장.메타읽기(conn, "시험2") == "1"

    def test_저장이_중간에_터지면_아무것도_안_남는다(self, conn):
        # 자식 삽입에서 터뜨린다 — 부모만 남으면 재개가 그걸 완성된 것으로 보고 건너뛴다.
        with pytest.raises(Exception):
            저장.저장(conn, "법령", _법령(), {"조문": [{"조문키": "x"}]})
        assert conn.execute("SELECT COUNT(*) FROM 법령").fetchone()[0] == 0


class Test날짜검증:
    @pytest.mark.parametrize(
        "값, 기대",
        [("0", None),                    # 헌재 종국일자 5,080건이 이 값이다
         ("20260229", None),             # 달력에 없는 날짜
         ("20240229", "2024-02-29"),     # 윤년은 통과
         ("42841231", "1951-12-31"),     # 단기(檀紀) → 서기. 판례 선고일자 45건
         ("2026.04.16", "2026-04-16"),
         ("20260416", "2026-04-16"),
         ("199919", None),               # 6자리. 행정규칙 시행일자 1건이 이 값이다
         ("20240600", None),             # 일=00. 행정심판례 의결일자 1건
         ("20030337", None),             # 일=37. 인용판례 피인용선고일 1건
         ("99991231", "9999-12-31"),     # 만료 없음의 센티널. 행정규칙 시행일자 1,695건
         ("", None), (None, None), ("이상한값", None)],
    )
    def test_달력에_없는_값과_단기를_거른다(self, 값, 기대):
        """⚠️ 그대로 저장하면 `종국일자 >= '2020-01-01'` 같은 범위 조건에서 조용히
        빠지거나(`'0'`), 단기 4284년이 **미래로 취급**된다."""
        assert 정규화.날짜(값) == 기대

    @pytest.mark.parametrize("값", ["2026-04-16", "1951-12-31", "9999-12-31"])
    def test_이미_ISO_면_그대로다(self, 값):
        """⚠️ **이게 이행의 멱등성을 떠받친다.** 마이그레이션이 저장된 값에 이 함수를
        다시 먹이는 방식이라, 여기서 두 번째 통과가 값을 바꾸면 매일 조금씩 달라진다."""
        assert 정규화.날짜(값) == 값


class Test감사가_빈DB를_잡는다:
    def test_빈_DB_는_게이트를_통과하면_안_된다(self, conn):
        """⚠️ 실제로 통과했다. 모든 게이트가 "잘못된 행이 없는가"만 물어서 행이 하나도
        없으면 전부 0 이 나왔다. **수집이 통째로 실패한 날 초록불이 뜨는 것**이
        이 DB 에서 가장 나쁜 결과다."""


        _, _, 실패 = 감사.run(conn)
        assert 실패 > 0


class Test드리프트:
    """**상수만 고치고 `migrate()` 를 안 돌리면 `.schema` 는 옛 주석을 그대로 내놓는다.**

    끊어진 고리가 여기다 — 상수→DB 갱신은 `수집.py` 가 실행 중 `migrate()` 를 부르는 것이
    전부인데, 원천 진단이 빨가면 수집을 건너뛰어 `migrate()` 도 안 돈다. `.gitignore` 가
    DB 를 빼므로 CI 는 접근조차 못 한다. **아무 신호가 없다는 것이 문제의 전부다.**
    """

    def test_동기화된_DB_는_통과한다(self, conn):


        assert conn.execute(감사.드리프트질의).fetchone()[0] == 0

    def test_주석_한_글자만_달라도_잡는다(self, conn):


        옛 = conn.execute("SELECT sql FROM sqlite_master WHERE name='법령'").fetchone()[0]
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name='법령'",
            (옛 + "  -- 몰래 붙은 주석",),
        )
        conn.execute("PRAGMA writable_schema=OFF")
        assert conn.execute(감사.드리프트질의).fetchone()[0] == 1

    def test_빈_DB_에서_빨갛다(self, db_path):
        """⚠️ 테이블이 하나도 없는 DB 를 통과시키면 게이트가 아니다."""


        빈 = sqlite3.connect(db_path)
        try:
            assert 빈.execute(감사.드리프트질의).fetchone()[0] == len(
                list(스키마._테이블문.finditer(스키마.SCHEMA))
            )
        finally:
            빈.close()

    def test_게이트_목록에_실제로_올라_있다(self):


        assert any(sql == 감사.드리프트질의 for _, _, sql in 감사.게이트)


class Test스키마출력:
    """`schema` 명령이 **드리프트 탐지기**이기도 하다."""

    def test_DB_가_있으면_DB_것을_낸다(self, conn, db_path):
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute(
            "UPDATE sqlite_master SET sql = sql || '  -- DB 에만 있는 표시'"
            " WHERE type='table' AND name='법령'"
        )
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
        본문, 경고 = 이행.스키마출력(db_path)
        assert "DB 에만 있는 표시" in 본문
        assert 경고 is not None and "낡았다" in 경고

    def test_동기화돼_있으면_경고가_없다(self, conn, db_path):
        본문, 경고 = 이행.스키마출력(db_path)
        assert 경고 is None and "CREATE TABLE 법령" in 본문

    def test_DB_가_없으면_상수를_내며_그렇다고_밝힌다(self, tmp_path):
        본문, 경고 = 이행.스키마출력(tmp_path / "없다.db")
        assert 본문 == 스키마.SCHEMA.strip()
        assert 경고 is not None and "상수" in 경고


class Test실패원장:
    def test_비어_있는_것이_정상이다(self, conn):
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 0

    def test_같은_대상을_다시_적으면_시도횟수가_는다(self, conn):
        저장.record_failure(conn, "판례", "1", "네트워크", "타임아웃")
        저장.record_failure(conn, "판례", "1", "네트워크", "또 타임아웃")
        r = conn.execute("SELECT 시도횟수, 메시지 FROM 수집실패").fetchone()
        assert r[0] == 2 and r[1] == "또 타임아웃"

    def test_없음은_재시도_대상에서_빠진다(self, conn):
        """⚠️ 재시도하면 매일 수만 건을 헛되이 두드린다."""
        저장.record_failure(conn, "판례", "없는것", "없음", "일치하는 판례가 없습니다")
        저장.record_failure(conn, "판례", "실패한것", "네트워크", "타임아웃")
        assert 저장.없음인것(conn, "판례") == {"없는것"}

    def test_성공하면_지워진다(self, conn):
        저장.record_failure(conn, "판례", "1", "네트워크")
        저장.clear_failure(conn, "판례", "1")
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 0


class Test진행상태:
    def test_기록하고_읽는다(self, conn):
        저장.상태기록(conn, "판례", "목록", 1, "완료", 1000)
        assert 저장.상태읽기(conn, "판례", "목록", 1) == "완료"
        assert 저장.상태읽기(conn, "판례", "목록", 2) is None

    def test_같은_키를_다시_기록하면_덮어쓴다(self, conn):
        저장.상태기록(conn, "판례", "목록", 1, "진행")
        저장.상태기록(conn, "판례", "목록", 1, "완료", 1000)
        assert 저장.상태읽기(conn, "판례", "목록", 1) == "완료"

    def test_메타(self, conn):
        assert 저장.메타읽기(conn, "스키마버전") == 이행.스키마버전
        저장.메타쓰기(conn, "파서버전", "2")
        assert 저장.메타읽기(conn, "파서버전") == "2"


class Test마이그레이션:
    # ⚠️ **앵커는 테이블 이름이다.** 특정 주석 문구를 박으면 그 주석을 손댄 날
    #    `.replace()` 가 아무것도 안 바꾸고 **테스트가 조용히 무의미해진다** —
    #    아래 `구조가 다르면` 검사가 같은 이유로 이미 이름 앵커를 쓰고 있다.
    _법령 = "CREATE TABLE IF NOT EXISTS 법령 (\n"

    def test_주석만_바뀌면_제자리에서_갈아_끼운다(self, conn):
        assert self._법령 in 스키마.SCHEMA
        새 = 스키마.SCHEMA.replace(self._법령, self._법령 + "  -- 바뀐 주석.\n", 1)
        assert 새 != 스키마.SCHEMA
        r = 이행.migrate(conn, 새, 백업확인=False)
        assert r["주석교체"] == ["법령"] and r["구조불일치"] == []
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='법령'"
        ).fetchone()[0]
        assert "바뀐 주석" in 저장된

    def test_바꿀_것이_없으면_아무것도_안_한다(self, conn):
        assert 이행.migrate(conn) == {"주석교체": [], "구조불일치": []}

    def test_구조가_다르면_손대지_않고_이름만_돌려준다(self, conn):
        """⚠️ `writable_schema` 는 검증 없이 카탈로그를 갈아 끼운다. 구조가 다른 DDL 을
        넣으면 테이블 실체와 카탈로그가 어긋난 채로 열리고, 그 상태는 조용하다."""
        # 앵커가 테이블 이름인 이유는 위 `_법령` 주석이 진다 —
        # 실제로 법령 마지막 컬럼에 주석을 붙였더니 옛 앵커가 다른 테이블로 옮겨갔다.
        assert self._법령 in 스키마.SCHEMA
        새 = 스키마.SCHEMA.replace(self._법령, self._법령 + "  새컬럼 TEXT,\n", 1)
        assert 새 != 스키마.SCHEMA
        r = 이행.migrate(conn, 새)
        assert r["구조불일치"] == ["법령"] and r["주석교체"] == []
        assert "새컬럼" not in conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='법령'"
        ).fetchone()[0]


class Test유령방지:
    """⚠️ `connect()` 가 부모 디렉터리를 만들고 평범하게 열기 때문에, 폴더를 옮기거나
    경로를 오타 낸 날 자동 수집은 **새 빈 DB 를 만들고 계속 초록으로 끝난다.** 감사는 전부
    현재 DB 안에서만 등식을 보므로 빈 DB 에서 다 통과하고, 의원실이 실제로 보는 DB 는
    그동안 조용히 낡는다 — **아무 에러도 나지 않는다.**

    LAW.db 는 수 시간짜리 전량 수집의 결과물이라 이 사고의 대가가 특히 크다.
    """

    def test_환경변수가_가리키는_DB_가_없으면_만들지_않는다(self, tmp_path, monkeypatch):
        없는것 = tmp_path / "옮겨진곳" / "LAW.db"
        monkeypatch.setenv("LAW_DB", str(없는것))
        with pytest.raises(SystemExit, match="가리키는 DB 가 없다"):
            연결.connect()
        assert not 없는것.exists() and not 없는것.parent.exists()

    def test_환경변수가_가리키는_DB_가_있으면_연다(self, tmp_path, monkeypatch):
        있는것 = tmp_path / "LAW.db"
        연결.connect(있는것).close()
        monkeypatch.setenv("LAW_DB", str(있는것))
        연결.connect().close()

    def test_경로를_손으로_대면_새로_만들_수_있다(self, tmp_path, monkeypatch):
        """**처음 만들 때는 경로를 명시하라**가 탈출구다."""
        monkeypatch.setenv("LAW_DB", str(tmp_path / "없는것.db"))
        새것 = tmp_path / "새로.db"
        연결.connect(새것).close()
        assert 새것.exists()

    def test_감사도_유령을_열지_않는다(self, tmp_path, monkeypatch):
        """`--db` 없이 부른 감사가 유령 DB 를 만들면 **게이트가 전부 초록으로 나온다** —
        빈 DB 에서는 모든 등식이 성립하기 때문이다. 그게 가장 나쁜 결과다."""
        monkeypatch.setenv("LAW_DB", str(tmp_path / "없다" / "LAW.db"))
        with pytest.raises(SystemExit, match="가리키는 DB 가 없다"):
            연결.connect()
