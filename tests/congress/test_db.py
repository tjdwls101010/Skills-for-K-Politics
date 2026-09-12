"""`db.py` 의 공개 경계."""

import re
import sqlite3
from pathlib import Path

import pytest

from congress import db

class Test연결:
    def test_새_연결마다_외래키가_켜진다(self, db_path):
        """S13. `journal_mode` 와 달리 `foreign_keys` 는 파일에 저장되지 않는다.

        수집 스크립트가 각자 별도 프로세스라 실행마다 새 연결이고, 연결 헬퍼를 거치지 않은
        경로가 하나라도 있으면 **스키마의 FK 전부가 장식이 되는데 아무 증상도 없다.**
        그래서 DDL 을 실행한 연결이 아니라 **나중에 새로 연 연결**에서 확인한다.
        """
        first = db.connect(db_path)
        db.init_schema(first)
        first.close()

        second = db.connect(db_path)
        assert second.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        second.close()

    def test_외래키가_실제로_위반을_막는다(self, conn):
        """PRAGMA 가 1이라는 것과 위반이 막힌다는 것은 다른 주장이다.

        계획 세션의 실측: 이 DDL 로 만든 DB 를 새 연결로 열면 존재하지 않는 부모를 참조하는
        INSERT 가 **에러 없이 성공했다.**
        """
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO 발의자 (의안번호, 의원코드, 역할) VALUES ('2200001','없는코드','대표발의')"
            )


class Test스키마:
    def test_모든_CREATE_TABLE_을_정규식이_잡는다(self):
        """⚠️ **`_테이블문` 이 못 잡은 테이블은 `migrate()` 의 사각지대다.**

        닫는 괄호를 줄 맨 앞에서만 찾으면 **한 줄로 쓴 `CREATE TABLE` 이 통째로 빠지고**,
        그 테이블의 주석만 DB 안에서 영원히 낡는데 아무 신호가 없다. law 에서 `메타` 가
        정확히 그랬다. 지금 congress 에는 한 줄짜리가 없지만, 컬럼 둘짜리 테이블을
        한 줄로 적고 싶어지는 날 이 검사가 막는다.
        """
        잡힌것 = {m.group(1) for m in db._테이블문.finditer(db.SCHEMA)}
        전부 = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\S+)", db.SCHEMA))
        assert 잡힌것 == 전부, f"정규식이 놓친 테이블: {sorted(전부 - 잡힌것)}"

    def test_경고_주석이_sqlite_master_에_살아남는다(self, conn):
        """S1. SQLite 는 `CREATE TABLE` **밖**의 주석을 버린다.

        옛 프로젝트는 주석 238줄 중 70줄(29%)이 `.schema` 에 도달하지 못한 채 초록불이었다.
        조회하는 쪽은 `.schema` 만 보므로, 버려진 경고는 **없는 것과 같다.**

        **0건이 정답이다**(law `test_경고생존.py` 와 같다). 한때 맨 위 `PRAGMA` 줄에 붙은
        포인터 주석 하나가 예외였는데, 그건 연결을 여는 사람에게 하는 말이지 조회자에게
        하는 말이 아니라 `connect()` docstring 으로 옮겼다 — **SQL 문자열 안은 조회자,
        밖은 개발자**라는 경계가 그 결정이다.
        """
        stored = "\n".join(
            r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
        )
        lost = [
            line.strip()
            for line in db.SCHEMA.splitlines()
            if "⚠️" in line and line.strip() not in stored
        ]
        assert lost == [], f"`.schema` 에 도달하지 못하는 경고: {lost}"

    def test_테이블_13개와_컬럼_74개(self, conn):
        tables = [
            r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        ]
        assert len(tables) == 13
        cols = sum(len(list(conn.execute(f'PRAGMA table_info("{t}")'))) for t in tables)
        assert cols == 74


class Test위원회_UPSERT:
    def test_저장된_이름을_돌려준다(self, conn):
        """S5. 넘긴 이름 그대로 자식에 넣으면 FK 위반이다.

        두 원천이 같은 위원회를 다르게 적는다 — `ALLNAMEMBER` 는 '기후위기 특별위원회',
        회의 열거 API 는 '기후위기특별위원회'. 나중에 온 표기로 자식 행을 넣으면
        **부모가 없어서 터지거나, FK 가 꺼져 있으면 조용히 고아 행이 된다.**
        """
        first = db.upsert_위원회(conn, "기후위기 특별위원회")
        second = db.upsert_위원회(conn, "기후위기특별위원회")

        assert first == "기후위기 특별위원회"
        assert second == first, "표기가 흔들려도 먼저 저장된 이름으로 모여야 한다"
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1

    def test_중점과_공백을_무시하고_같은_것으로_본다(self, conn):
        db.upsert_위원회(conn, "대법관(노경필·박영재·이숙연)임명동의에관한인사청문특별위원회")
        again = db.upsert_위원회(conn, "대법관(노경필ㆍ박영재ㆍ이숙연) 임명동의에 관한 인사청문특별위원회")
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1
        assert again == "대법관(노경필·박영재·이숙연)임명동의에관한인사청문특별위원회"

    def test_파이썬_정규형과_SQL_정규형이_같은_집합을_지운다(self, conn):
        """실측 회귀: 소위 심사 수집에서 `UNIQUE constraint failed` 로 터졌다.

        처음엔 파이썬만 정규식 `[\\s·ㆍ・]` 을 썼는데 `\\s` 는 유니코드 공백까지 지우고
        SQL 의 REPLACE 목록은 안 지웠다. 그러면 **조회가 못 찾아 INSERT 로 빠지는데
        그 이름이 이미 있어서** UNIQUE 위반이 난다. 실제 이름:

            '법제사법위원회 안건조정위원회\\u3000\\u3000\\u3000\\u3000(2026. 1. 7. 구성)'
        """
        이름 = "법제사법위원회 안건조정위원회　　(2026. 1. 7. 구성)"
        첫 = db.upsert_위원회(conn, 이름)
        둘 = db.upsert_위원회(conn, 이름)
        assert 첫 == 둘 == 이름
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1

        # 공백만 다른 표기도 같은 행으로 모여야 한다
        셋 = db.upsert_위원회(conn, "법제사법위원회 안건조정위원회 (2026. 1. 7. 구성)")
        assert 셋 == 이름
        assert conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0] == 1

    def test_소위를_먼저_넣어도_상위가_선행_삽입된다(self, conn):
        """S6. 자기참조 FK 라 상위가 없으면 INSERT 가 터진다."""
        name = db.upsert_위원회(
            conn, "법제사법위원회 법안심사제1소위원회", 상위="법제사법위원회"
        )
        rows = dict(conn.execute("SELECT 위원회명, 상위위원회 FROM 위원회").fetchall())
        assert rows == {
            "법제사법위원회": None,
            "법제사법위원회 법안심사제1소위원회": "법제사법위원회",
        }
        assert name == "법제사법위원회 법안심사제1소위원회"

    def test_이름을_쪼개서_상위를_추측하지_않는다(self, conn):
        """S20. 상위를 명시하지 않으면 NULL 이다 — 공백 분리를 하지 않는다.

        '기후위기 특별위원회 탄소중립기본법심사소위원회' 를 공백으로 쪼개면 상위가
        '기후위기' 가 되는데, **에러가 안 나고 없는 위원회가 하나 생긴다.**
        상위는 언제나 원천(`nkimylolanvseqagq.CMIT_NM`)이 말해 준다.
        """
        db.upsert_위원회(conn, "기후위기 특별위원회 탄소중립기본법심사소위원회")
        rows = dict(conn.execute("SELECT 위원회명, 상위위원회 FROM 위원회").fetchall())
        assert rows == {"기후위기 특별위원회 탄소중립기본법심사소위원회": None}

    def test_상위를_나중에_알게_되면_채운다(self, conn):
        """다른 패스가 이름만 먼저 등록해 두는 일이 실제로 생긴다.

        수집 순서상 `위원회` 를 먼저 세우지만, 의안·회의 패스가 처음 보는 이름을 만나면
        상위를 모르는 채로 UPSERT 한다. 나중에 원천이 상위를 알려줬을 때 덮어써야 한다.
        """
        db.upsert_위원회(conn, "보건복지위원회 법안심사제1소위원회")
        db.upsert_위원회(conn, "보건복지위원회 법안심사제1소위원회", 상위="보건복지위원회")
        assert conn.execute(
            "SELECT 상위위원회 FROM 위원회 WHERE 위원회명='보건복지위원회 법안심사제1소위원회'"
        ).fetchone()[0] == "보건복지위원회"

    def test_이미_있는_상위를_NULL_로_되돌리지_않는다(self, conn):
        """상위를 아는 원천과 모르는 원천이 번갈아 같은 행을 쓴다.

        모르는 쪽이 나중에 오면 `SET 상위위원회=NULL` 이 되어 **소위가 소위가 아니게 된다.**
        `위원회.상위위원회` 가 "소위인가"의 유일한 답이므로 그 순간 소위 조회가 조용히 빈다.
        """
        db.upsert_위원회(conn, "정무위원회 법안심사제1소위원회", 상위="정무위원회")
        db.upsert_위원회(conn, "정무위원회 법안심사제1소위원회")
        assert conn.execute(
            "SELECT 상위위원회 FROM 위원회 WHERE 위원회명='정무위원회 법안심사제1소위원회'"
        ).fetchone()[0] == "정무위원회"


class Test의원_정당확인일:
    def test_정당확인일_컬럼이_있다(self, conn):
        """현직 API 로 정당을 마지막으로 확인한 날. NULL = 한 번도 확인 못 한 전직(당선 당시 정당)."""
        컬럼 = {r[1] for r in conn.execute("PRAGMA table_info(의원)")}
        assert "정당확인일" in 컬럼
