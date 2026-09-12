"""`load.py` — 스키마 · 연결 · 쓰기 경로 · 마이그레이션."""

import re
import sqlite3

import pytest

from law import audit as 감사
from law import schema as 스키마
from law import conn as 연결
from law import migrate as 이행
from law import store as 저장
from law import normalize as 정규화


class Test스키마:
    def test_두_번_적용해도_같다(self, conn):
        이행.init_schema(conn)
        n = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
        assert n == 10  # 데이터 8 + 운영 2

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
    **우리 설계가 집합을 닫은 자리에만 건다** — 원천이 값을 늘릴 수 있는 `법종구분`·
    `제개정구분`·`사건종류` 에는 안 건다. 그건 레일이 된다.
    """

    @pytest.mark.parametrize(
        "무엇, sql",
        [("의율조문.자료종류",
          "INSERT INTO 의율조문 (자료종류, 자료ID, 출처, 법령명원문, 원문조각)"
          " VALUES ('위원회결정', '1', '참조조문', '개인정보 보호법', 'x')"),
         ("의율조문.출처",
          "INSERT INTO 의율조문 (자료종류, 자료ID, 출처, 법령명원문, 원문조각)"
          " VALUES ('판례', '1', '이유', '개인정보 보호법', 'x')"),
         ("의율조문.부칙여부",
          "INSERT INTO 의율조문 (자료종류, 자료ID, 출처, 법령명원문, 부칙여부, 원문조각)"
          " VALUES ('판례', '1', '참조조문', '개인정보 보호법', 2, 'x')"),
         # 인용판례는 둘뿐이다 — 판례·헌재만 참조판례를 준다
         ("인용판례.자료종류",
          "INSERT INTO 인용판례 (자료종류, 자료ID, 피인용사건번호, 원문조각)"
          " VALUES ('법령해석례', '1', '2018다244488', 'x')"),
         ("인용판례.피인용종류",
          "INSERT INTO 인용판례 (자료종류, 자료ID, 피인용종류, 피인용사건번호, 원문조각)"
          " VALUES ('판례', '1', '행정심판례', '2018다244488', 'x')"),
         ("위임.위임구분",
          "INSERT INTO 위임 (법령ID, 순서, 조, 위임구분, 대상제목)"
          " VALUES ('011357', 0, 8, '인용법령', 'x')"),
         ("판례.형식",
          "INSERT INTO 판례 (판례ID, 사건번호, 형식, 수집일시)"
          " VALUES ('1', '2024다1', '재판', 'x')"),
         ("판례.전원합의체",
          "INSERT INTO 판례 (판례ID, 사건번호, 전원합의체, 수집일시)"
          " VALUES ('1', '2024다1', 9, 'x')"),
         # 전문·판시사항·결정요지가 다 없는 껍데기는 담지 않는다
         ("헌재결정례.본문셋",
          "INSERT INTO 헌재결정례 (헌재결정례ID, 사건번호, 전문, 수집일시)"
          " VALUES ('1', '2015헌마1', '', 'x')"),
         # 시행예정 두 컬럼은 함께 있거나 함께 없다
         ("법령.시행예정쌍",
          "INSERT INTO 법령 (법령ID, 법령일련번호, 법령명, 법종구분, 시행예정일련번호, 수집일시)"
          " VALUES ('X', '9', 'x', '법률', '283839', 'x')"),
         ("수집상태.단계",
          "INSERT INTO 수집상태 (자료종류, 단계, 상태, 갱신일시, 상태시작일시)"
          " VALUES ('판례', '본문', '완료', 'x', 'x')"),
         ("수집상태.상태",
          "INSERT INTO 수집상태 (자료종류, 단계, 상태, 갱신일시, 상태시작일시)"
          " VALUES ('판례', '목록', '부분수신', 'x', 'x')"),
         ("수집실패.실패종류",
          "INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 최초일시, 최종일시)"
          " VALUES ('판례', '1', '네트워크', 'x', 'x')")],
    )
    def test_닫아_둔_값집합_밖은_INSERT_가_막는다(self, conn, 무엇, sql):
        if 무엇 == "위임.위임구분":  # FK 가 먼저 걸리지 않게 부모를 둔다
            저장.저장(conn, "법령", _법령())
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(sql)

    @pytest.mark.parametrize("표, 컬럼", [
        ("법령", "법령ID"), ("판례", "판례ID"),
        ("헌재결정례", "헌재결정례ID"), ("법령해석례", "법령해석례ID"),
    ])
    def test_원천_손잡이는_NULL_이_될_수_없다(self, conn, 표, 컬럼):
        """⚠️ **SQLite 의 TEXT PRIMARY KEY 는 NULL 을 허용한다.** NOT NULL 을 안 붙이면
        키 없는 행이 들어가고, 그 행은 어느 조인에도 안 걸리면서 건수에는 잡힌다."""
        본문 = ", 전문" if 표 == "헌재결정례" else ""
        값 = ", '전문'" if 표 == "헌재결정례" else ""
        나머지 = {"법령": ", 법령일련번호, 법령명, 법종구분", "판례": ", 사건번호",
                "헌재결정례": ", 사건번호", "법령해석례": ", 안건번호, 안건명"}[표]
        더 = {"법령": ", '9', 'x', '법률'", "판례": ", '2024다1'",
             "헌재결정례": ", '2015헌마1'", "법령해석례": ", '20-0370', 'x'"}[표]
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            conn.execute(
                f"INSERT INTO {표} ({컬럼}{나머지}{본문}, 수집일시)"
                f" VALUES (NULL{더}{값}, 'x')")

    def test_옳은_값은_그대로_들어간다(self, conn):
        """⚠️ 막는 것만 시험하면 **전부 막는 CHECK** 도 통과한다."""
        저장.저장(conn, "법령", _법령())
        for 종류 in ("판례", "헌재결정례", "법령해석례"):
            for 출처 in ("참조조문", "심판대상조문", "안건명"):
                conn.execute(
                    "INSERT INTO 의율조문 (자료종류, 자료ID, 출처, 법령명원문, 원문조각)"
                    " VALUES (?, '1', ?, '개인정보 보호법', 'x')", (종류, 출처))
        assert conn.execute("SELECT COUNT(*) FROM 의율조문").fetchone()[0] == 9


class Test뷰:
    """**뷰는 산문이 지키던 고정 조인을 인터페이스로 옮긴 것이다.**

    `.schema` 안에 살아 매 세션 공짜로 다시 읽히고, 지면의 SQL 한 덩어리는 읽고 옮겨
    적어야 한다. 내용은 `test_새뷰.py` 가 보고, 여기서는 **정의가 갱신되는가**만 본다.
    """

    def test_뷰_정의를_고치면_기존_DB_에도_반영된다(self, conn):
        """⚠️ **`CREATE VIEW IF NOT EXISTS` 는 정의가 바뀌어도 갱신하지 않는다.**
        그대로 두면 뷰가 이 DB 안에서 낡고, `.schema` 를 읽는 클로드는 낡은 정의를
        정본으로 읽는다 — 테이블 주석에 `migrate()` 가 필요한 것과 같은 이유다."""
        conn.execute("DROP VIEW 조문판단수")
        conn.execute("CREATE VIEW 조문판단수 AS SELECT 법령ID FROM 조문")  # 낡은 정의
        이행.init_schema(conn)
        저장된 = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='view' AND name='조문판단수'"
        ).fetchone()[0]
        assert "판례수" in 저장된


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
                "INSERT INTO 조문 (법령ID, 순서, 조, 가지, 전문)"
                " VALUES ('없는법령', 0, 1, 0, 'x')"
            )

    def test_db_경로는_환경변수가_기본값을_이긴다(self, tmp_path, monkeypatch):
        # GitHub Actions 가 DB 를 체크아웃 밖으로 보내는 유일한 수단이다.
        monkeypatch.setenv("LAW_DB", str(tmp_path / "환경.db"))
        assert 연결.db_path() == tmp_path / "환경.db"
        assert 연결.db_path(tmp_path / "명시.db") == tmp_path / "명시.db"

    def test_환경변수가_없으면_스킬_디렉터리다(self, monkeypatch):
        monkeypatch.delenv("LAW_DB", raising=False)
        assert 연결.db_path().name == "LAW.db"
        assert 연결.db_path().parent.name == "DBs"


def _법령(법령ID="011357", mst="270351", 명="개인정보 보호법"):
    return {
        "법령ID": 법령ID,
        "법령일련번호": mst,
        "법령명": 명,
        "법종구분": "법률",
    }


def _조문(순서=0, 조=15, 전문="제15조 …"):
    return {"순서": 순서, "조": 조, "가지": 0, "전문": 전문}


class Test저장:
    def test_본문과_자식이_함께_들어간다(self, conn):
        저장.저장(conn, "법령", _법령(), {"조문": [_조문()]})
        assert conn.execute("SELECT COUNT(*) FROM 법령").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM 조문").fetchone()[0] == 1

    def test_수집일시가_자동으로_채워진다(self, conn):
        저장.저장(conn, "법령", _법령())
        assert conn.execute("SELECT 수집일시 FROM 법령").fetchone()[0]

    def test_같은_법령ID_는_한_행뿐이다(self, conn):
        """⚠️ **판본이 아니라 법이 행이다.** 두 행이 되면 판본을 안 보고 조문을 세는
        질의가 같은 조를 판본 수만큼 곱해 돌려준다 — 에러 없이."""
        저장.저장(conn, "법령", _법령(mst="270351"))
        저장.저장(conn, "법령", _법령(mst="283839"))
        r = conn.execute("SELECT COUNT(*), MAX(법령일련번호) FROM 법령").fetchone()
        assert tuple(r) == (1, "283839")

    def test_저장하면_수집실패에서_지워진다(self, conn):
        저장.record_failure(conn, "법령", "011357", "재시도", "타임아웃")
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
        with pytest.raises(ValueError, match="법령ID"):
            저장.저장(conn, "법령", {**_법령(), "법령ID": ""})

    def test_네_종_전부_저장_경로가_있다(self, conn):
        assert set(저장.본문테이블) == {"법령", "판례", "헌재결정례", "법령해석례"}
        저장.저장(conn, "판례", {"판례ID": "1", "사건번호": "2026도477"})
        저장.저장(conn, "헌재결정례",
                {"헌재결정례ID": "1", "사건번호": "2015헌마1140", "전문": "…"})
        저장.저장(conn, "법령해석례",
                {"법령해석례ID": "1", "안건번호": "20-0370", "안건명": "…"})
        저장.저장(conn, "법령", _법령(), {"조문": [_조문()]})
        for 테이블 in ("판례", "헌재결정례", "법령해석례", "조문"):
            assert conn.execute(f"SELECT COUNT(*) FROM {테이블}").fetchone()[0] == 1


class Test본문지우기:
    """철회·정리가 본문을 지울 때 **파생 행도 함께** 지운다. 남으면 없는 판단을 가리키는
    행이 되고, `인용판례` 는 게이트도 없어 조용하다."""

    def _판단둘(self, conn):
        저장.저장(conn, "판례", {"판례ID": "P1", "사건번호": "2024다1"})
        저장.저장(conn, "판례", {"판례ID": "P2", "사건번호": "2024다2"})
        저장.저장(conn, "헌재결정례",
                {"헌재결정례ID": "P1", "사건번호": "2020헌바1", "전문": "…"})
        conn.execute(
            "INSERT INTO 의율조문 (자료종류, 자료ID, 출처, 법령명원문, 원문조각)"
            " VALUES ('판례','P1','참조조문','민법','x')")
        conn.executemany(
            "INSERT INTO 인용판례 (자료종류, 자료ID, 피인용종류, 피인용자료ID,"
            " 피인용사건번호, 원문조각) VALUES (?,?,?,?,?,'x')",
            [("판례", "P1", "판례", "P2", "2024다2"),
             ("판례", "P2", "판례", "P1", "2024다1"),
             ("판례", "P2", "헌재결정례", "P1", "2020헌바1")])

    def test_본문과_파생_행을_함께_지운다(self, conn):
        self._판단둘(conn)
        저장.본문지우기(conn, "판례", ["P1"])
        assert conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM 의율조문").fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM 인용판례 WHERE 자료ID='P1'").fetchone()[0] == 0

    def test_남의_인용행에_박힌_링크를_끊는다(self, conn):
        """⚠️ 파생 삭제는 인용한 쪽만 본다. 그대로 두면 `피인용자료ID` 가 **없는 자료를
        가리키고**, NULL 이 곧 "적재 범위 밖"이라는 뜻이니 끊는 순간 그게 사실이 된다."""
        self._판단둘(conn)
        저장.본문지우기(conn, "판례", ["P1"])
        assert conn.execute(
            "SELECT 피인용자료ID FROM 인용판례 WHERE 자료ID='P2' AND 피인용종류='판례'"
        ).fetchone()[0] is None

    def test_일련번호가_겹치는_남의_표는_안_끊는다(self, conn):
        """⚠️ 판례와 헌재결정례는 일련번호가 겹칠 수 있다. `피인용종류` 없이 ID 로만
        가르면 멀쩡한 헌재 링크가 함께 끊긴다."""
        self._판단둘(conn)
        저장.본문지우기(conn, "판례", ["P1"])
        assert conn.execute(
            "SELECT 피인용자료ID FROM 인용판례 WHERE 피인용종류='헌재결정례'"
        ).fetchone()[0] == "P1"


class Test철회:
    def test_본문을_지우고_원장에_남긴다(self, conn):
        """⚠️ **행을 남기고 표지만 다는 옛 정책을 뒤집었다.** 새 뷰에 철회 표지가 없어서,
        행을 남기면 **철회된 판단이 정상 판단처럼 보인다.**"""
        저장.저장(conn, "판례", {"판례ID": "P1", "사건번호": "2024다1"})
        assert 저장.철회기록(conn, "판례", ["P1"]) == 1
        assert conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0] == 0
        assert 저장.철회된것(conn, "판례") == {"P1"}

    def test_철회와_없음이_한_자료에_함께_남지_않는다(self, conn):
        """⚠️ 둘 다 "원천에 본문이 없다"의 기록이라, 양쪽에 남으면 적재 등식이 같은 행을
        두 번 기대해 **고칠 수 없는 빨간불**이 된다."""
        저장.저장(conn, "판례", {"판례ID": "P1", "사건번호": "2024다1"})
        저장.record_failure(conn, "판례", "P1", "없음", "일치하는 판례가 없습니다")
        저장.철회기록(conn, "판례", ["P1"])
        assert conn.execute(
            "SELECT COUNT(*) FROM 수집실패 WHERE 자료ID='P1'").fetchone()[0] == 1

    def test_본문을_다시_받으면_표지가_지워진다(self, conn):
        저장.저장(conn, "판례", {"판례ID": "P1", "사건번호": "2024다1"})
        저장.철회기록(conn, "판례", ["P1"])
        저장.저장(conn, "판례", {"판례ID": "P1", "사건번호": "2024다1"})
        assert 저장.철회된것(conn, "판례") == set()


class Test트랜잭션:
    """⚠️ `connect()` 가 `isolation_level=None`(autocommit)로 열기 때문에 **`with conn:` 은
    트랜잭션을 시작하지 않는다.** 실제로 그렇게 써 놨었고, 블록 중간에 터져도 이미 쓴 것이
    그대로 남았다. 반쪽 레코드가 영구 커밋되고 다음 실행이 그걸 완성된 것으로 본다."""

    def _센다(conn):
        return conn.execute("SELECT COUNT(*) FROM 판례").fetchone()[0]

    def test_예외가_나면_되돌린다(self, conn):
        with pytest.raises(RuntimeError):
            with 연결.트랜잭션(conn):
                conn.execute(
                    "INSERT INTO 판례 (판례ID, 사건번호, 수집일시)"
                    " VALUES ('시험', 'x', 'x')")
                raise RuntimeError("중간에 터진다")
        assert Test트랜잭션._센다(conn) == 0

    def test_with_conn_은_되돌리지_않는다(self, conn):
        # 이 테스트는 **왜 트랜잭션() 이 필요한지**를 잠근다. 여기가 깨지면
        # 파이썬 sqlite3 의 동작이 바뀐 것이므로 트랜잭션() 을 다시 검토해야 한다.
        with pytest.raises(RuntimeError):
            with conn:
                conn.execute(
                    "INSERT INTO 판례 (판례ID, 사건번호, 수집일시)"
                    " VALUES ('시험2', 'x', 'x')")
                raise RuntimeError("x")
        assert Test트랜잭션._센다(conn) == 1

    def test_저장이_중간에_터지면_아무것도_안_남는다(self, conn):
        # 자식 삽입에서 터뜨린다 — 부모만 남으면 재개가 그걸 완성된 것으로 보고 건너뛴다.
        with pytest.raises(Exception):
            저장.저장(conn, "법령", _법령(), {"조문": [{"순서": 0}]})
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
         ("199919", None),               # 6자리
         ("20240600", None),             # 일=00
         ("20030337", None),             # 일=37. 인용판례 피인용선고일 1건
         ("99991231", "9999-12-31"),     # 만료 없음의 센티널
         ("", None), (None, None), ("이상한값", None)],
    )
    def test_달력에_없는_값과_단기를_거른다(self, 값, 기대):
        """⚠️ 그대로 저장하면 `종국일자 >= '2020-01-01'` 같은 범위 조건에서 조용히
        빠지거나(`'0'`), 단기 4284년이 **미래로 취급**된다."""
        assert 정규화.날짜(값) == 기대

    @pytest.mark.parametrize("값", ["2026-04-16", "1951-12-31", "9999-12-31"])
    def test_이미_ISO_면_그대로다(self, 값):
        """⚠️ **멱등이어야 한다.** 변환 스크립트가 저장된 값에 이 함수를 다시 먹이므로,
        여기서 두 번째 통과가 값을 바꾸면 값이 매번 조금씩 달라진다."""
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

    끊어진 고리가 여기다 — 상수→DB 갱신은 `collect.py` 가 실행 중 `migrate()` 를 부르는 것이
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
    def test_재시도는_비어_있는_것이_정상이다(self, conn):
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 0

    def test_같은_대상을_다시_적으면_시도횟수가_는다(self, conn):
        저장.record_failure(conn, "판례", "1", "재시도", "타임아웃")
        저장.record_failure(conn, "판례", "1", "재시도", "또 타임아웃")
        r = conn.execute("SELECT 시도횟수, 메시지 FROM 수집실패").fetchone()
        assert r[0] == 2 and r[1] == "또 타임아웃"

    def test_실패종류가_바뀌면_시도횟수가_1_로_돌아간다(self, conn):
        """⚠️ '재시도' 3회를 쌓은 자료가 '목록누락'으로 바뀌면서 그 3을 물려받으면,
        연속 누락 상한이 **첫 누락에서 이미 채워져** 하루 유예가 통째로 우회된다."""
        for _ in range(3):
            저장.record_failure(conn, "판례", "1", "재시도", "타임아웃")
        저장.record_failure(conn, "판례", "1", "목록누락", "완결 목록에 없다")
        assert conn.execute("SELECT 시도횟수 FROM 수집실패").fetchone()[0] == 1

    @pytest.mark.parametrize("실패종류", ["없음", "본문없음", "철회"])
    def test_본문이_없다는_기록은_재시도_대상에서_빠진다(self, conn, 실패종류):
        """⚠️ 셋 다 원천의 답이라 다시 물어도 같은 답이 온다 — 재시도하면 매일 수만 건을
        헛되이 두드린다."""
        저장.record_failure(conn, "판례", "없는것", 실패종류, "…")
        저장.record_failure(conn, "판례", "실패한것", "재시도", "타임아웃")
        assert 저장.없음인것(conn, "판례") == {"없는것"}

    def test_성공하면_지워진다(self, conn):
        저장.record_failure(conn, "판례", "1", "재시도")
        저장.clear_failure(conn, "판례", "1")
        assert conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0] == 0


class Test수집상태:
    def test_기록하고_읽는다(self, conn):
        저장.상태기록(conn, "판례", "목록", "완료", 1000)
        assert 저장.상태읽기(conn, "판례", "목록") == "완료"
        assert 저장.상태읽기(conn, "판례", "정리") is None

    def test_자료종류_단계마다_한_행이다(self, conn):
        """실행 이력이 아니라 **지금 상태**다. 이력을 쌓으면 "지금 어떤가"를 묻는
        질의가 매번 정렬과 LIMIT 을 달아야 한다."""
        저장.상태기록(conn, "판례", "목록", "부분", 900)
        저장.상태기록(conn, "판례", "목록", "완료", 1000)
        행 = conn.execute("SELECT 상태, 건수 FROM 수집상태").fetchall()
        assert [tuple(r) for r in 행] == [("완료", 1000)]

    def test_같은_상태가_이어지면_시작_시각이_보존된다(self, conn):
        """⚠️ `갱신일시` 는 매 실행 덮인다. 며칠째 이 상태인지는 `상태시작일시` 만 안다."""
        저장.상태기록(conn, "판례", "정리", "부분")
        시작 = conn.execute("SELECT 상태시작일시 FROM 수집상태").fetchone()[0]
        저장.상태기록(conn, "판례", "정리", "부분")
        assert conn.execute("SELECT 상태시작일시 FROM 수집상태").fetchone()[0] == 시작

    def test_상태가_바뀌면_시작_시각이_다시_잡힌다(self, conn):
        저장.상태기록(conn, "판례", "정리", "부분")
        저장.상태기록(conn, "판례", "정리", "완료", 0)
        r = conn.execute("SELECT 갱신일시, 상태시작일시 FROM 수집상태").fetchone()
        assert r[0] == r[1]

    def test_기준선은_자격이_있을_때만_갈린다(self, conn):
        """⚠️ 부분 수신·급감이 기준선을 덮으면 다음 실행이 그 낮은 값과 비교해 통과하고,
        크기 가드가 **삭제를 하루 늦추는 것 이상을 못 한다.**"""
        저장.기준선기록(conn, "판례", 1000, 갱신=True, 검증된목록=True)
        assert 저장.기준선읽기(conn, "판례") == 1000
        저장.기준선기록(conn, "판례", 400, 갱신=True, 검증된목록=False)
        assert 저장.기준선읽기(conn, "판례") == 1000
        assert 저장.상태읽기(conn, "판례", "목록") == "부분"
        저장.기준선기록(conn, "판례", 400, 갱신=False, 검증된목록=True)
        assert 저장.기준선읽기(conn, "판례") == 1000
        assert 저장.상태읽기(conn, "판례", "목록") == "이상"

    def test_수집끝냈다가_신선도의_적재기준시각이_된다(self, conn):
        """⚠️ **행을 하나도 안 썼어도 적는다.** 밀린 것이 없어 조용히 끝난 실행과 수집기가
        며칠째 안 도는 상태는 전혀 다른 일인데, 행이 있을 때만 적으면 둘이 똑같아 보인다."""
        assert conn.execute("SELECT 적재기준시각 FROM 신선도").fetchone()[0] is None
        저장.수집끝냈다(conn)
        assert conn.execute("SELECT 적재기준시각 FROM 신선도").fetchone()[0]


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
