"""위임행정규칙의 끊긴 연결을 잇는다 — `위임.대상행정규칙ID` · `행정규칙근거` 3분기 · 해결률 · 락 · 이름.

실측(2026-09-10·11): 위임행정규칙 대상 11,841 중 일련번호가 우리 `행정규칙` 에 그대로 있는 것은 4,001
뿐이다 — 원천이 옛 판본의 일련번호를 준다(전자금융감독규정: 위임 `2100000061069` vs 우리 `…282622`).
본문 API(`admrul&ID=옛일련번호`)는 그 옛 판본을 열어 **불변 식별자 `행정규칙ID`(21828)** 를 준다 —
그것으로 현행 판본에 닿는다. 이름 매칭은 확정이 아니다: 동명 현행 규칙 30건(ID 17·부처 12)이 실재한다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from law import audit as 감사
from law.collectors import run as 실행
from law import schema as 스키마
from law import conn as 연결
from law import source as 원천
from law.collectors import delegation as 위임
from law import migrate as 이행





SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts" / "law"


def 법령(conn, mst, 법령ID, 이름):
    conn.execute(
        "INSERT INTO 법령 (법령일련번호,법령ID,법령명,법종구분,시행일자,현행연혁코드,수집일시)"
        " VALUES (?,?,?,'법률','2024-01-01','현행','2026-09-11 00:00:00')", (mst, 법령ID, 이름))


def 규칙(conn, 일련번호, ID, 이름, 현행="Y", 부처="금융위원회", 발령="2024-01-01"):
    conn.execute(
        "INSERT INTO 행정규칙 (행정규칙일련번호,행정규칙ID,행정규칙명,행정규칙종류,발령일자,소관부처명,현행여부,수집일시)"
        " VALUES (?,?,?,'고시',?,?,?,'2026-09-11 00:00:00')", (일련번호, ID, 이름, 발령, 부처, 현행))


def 위임행(conn, mst, 순서, 조, 구분, 제목, 대상일련번호=None, 대상행정규칙ID=None):
    conn.execute(
        "INSERT INTO 위임 (법령일련번호,순서,조문번호,조문가지번호,위임구분,대상제목,대상일련번호,대상행정규칙ID)"
        " VALUES (?,?,?,0,?,?,?,?)", (mst, 순서, 조, 구분, 제목, 대상일련번호, 대상행정규칙ID))


class Test스키마:
    def test_위임에_대상행정규칙ID_가_마지막_컬럼이다(self, conn):
        """`컬럼보강` 이 `ALTER TABLE ADD COLUMN` 으로 붙이므로 SCHEMA 에서도 맨 뒤여야 한다."""
        컬럼 = [r[1] for r in conn.execute("PRAGMA table_info(위임)")]
        assert 컬럼[-1] == "대상행정규칙ID"

    def test_옛_DB_에는_컬럼보강이_붙인다(self, db_path):
        c = 연결.connect(db_path)
        옛 = 스키마.SCHEMA.split("DROP VIEW", 1)[0]                       # 표·인덱스만 — 옛 DB 에 뷰는 init 이 다시 만든다
        옛 = re.sub(r"\n[^\n]*대상행정규칙ID[^\n]*", "", 옛)            # 컬럼 선언과 그 인덱스
        assert "대상행정규칙ID" not in 옛
        이행._스키마적용(c, 옛)
        assert "위임.대상행정규칙ID" in 이행.컬럼보강(c)
        c.close()


class 가짜클라이언트:
    def __init__(self, 답: dict):
        self.답, self.호출 = 답, []

    def 본문하나(self, 종류, 식별값, **_):
        self.호출.append(식별값)
        if 식별값 not in self.답:
            raise 원천.원천에없음(f"{식별값} 없음")
        return {"행정규칙기본정보": {"행정규칙ID": self.답[식별값], "행정규칙일련번호": 식별값}}


@pytest.fixture
def 위임DB(conn):
    법령(conn, "254921", "010199", "전자금융거래법")
    규칙(conn, "2100000282622", "21828", "전자금융감독규정")
    위임행(conn, "254921", 0, 21, "위임행정규칙", "전자금융감독규정", 대상일련번호="2100000061069")   # 옛 판본
    위임행(conn, "254921", 1, 22, "위임행정규칙", "전자금융감독규정", 대상일련번호="2100000282622")   # 우리 것
    위임행(conn, "254921", 2, 23, "위임행정규칙", "사라진 규칙", 대상일련번호="2100000000001")
    위임행(conn, "254921", 3, 24, "시행령", "전자금융거래법 시행령", 대상일련번호="123456")          # 대상 아님
    conn.commit()
    return conn


class Test해결:
    def test_우리_행정규칙에_있는_일련번호는_호출_없이_ID_를_채운다(self, 위임DB):
        c = 가짜클라이언트({})
        위임.해결(위임DB, c, 로그=lambda _: None)
        assert "2100000282622" not in c.호출
        assert 위임DB.execute("SELECT 대상행정규칙ID FROM 위임 WHERE 순서=1").fetchone()[0] == "21828"

    def test_옛_일련번호는_본문_API_로_ID_를_얻는다(self, 위임DB):
        c = 가짜클라이언트({"2100000061069": "21828"})
        수치 = 위임.해결(위임DB, c, 로그=lambda _: None)
        assert 위임DB.execute("SELECT 대상행정규칙ID FROM 위임 WHERE 순서=0").fetchone()[0] == "21828"
        assert 수치["ID"] >= 1

    def test_원천에_없으면_없음으로_남기고_다시_묻지_않는다(self, 위임DB):
        c = 가짜클라이언트({"2100000061069": "21828"})
        위임.해결(위임DB, c, 로그=lambda _: None)
        assert 위임DB.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 자료종류='위임행정규칙' AND 자료ID='2100000000001'"
        ).fetchone()[0] == "없음"
        c2 = 가짜클라이언트({})
        위임.해결(위임DB, c2, 로그=lambda _: None)
        assert c2.호출 == []                                  # 채운 것도, 없음도 다시 안 묻는다

    def test_시행령_위임은_대상이_아니다(self, 위임DB):
        assert "123456" not in 위임.해결대상(위임DB)

    def test_수집_순서에서_위임_바로_뒤다(self):
        이름들 = [n for n, _ in 실행.순서]
        assert 이름들.index("위임해결") == 이름들.index("위임") + 1
        assert dict(실행.순서)["위임해결"] is 위임.해결


@pytest.fixture
def 근거DB(conn):
    법령(conn, "254921", "010199", "전자금융거래법")
    규칙(conn, "2100000282622", "21828", "전자금융감독규정")
    규칙(conn, "2100000061069", "21828", "전자금융감독규정", 현행="N")           # 옛 판본도 우리에게 있는 경우
    규칙(conn, "2100000000010", "10", "동명규칙", 부처="A부")
    규칙(conn, "2100000000011", "11", "동명규칙", 부처="B부")
    규칙(conn, "2100000000012", "12", "동명규칙", 현행="N")
    위임행(conn, "254921", 0, 21, "위임행정규칙", "전자금융감독규정", 대상일련번호="2100000061069", 대상행정규칙ID="21828")
    위임행(conn, "254921", 1, 22, "위임행정규칙", "전자금융감독규정", 대상일련번호="2100000282622")
    위임행(conn, "254921", 2, 23, "위임행정규칙", "동명규칙", 대상일련번호="2100000009999")
    위임행(conn, "254921", 3, 24, "위임행정규칙", "없는 규칙", 대상일련번호="2100000009998")
    conn.commit()
    return conn


class Test행정규칙근거:
    def _행들(self, conn, 조):
        return conn.execute(
            "SELECT 해결, 행정규칙일련번호, 행정규칙ID, 후보수, 소관부처명, 발령일자 FROM 행정규칙근거"
            " WHERE 조문번호=? ORDER BY 행정규칙일련번호", (조,)).fetchall()

    def test_ID_로_현행_판본에_닿는다(self, 근거DB):
        행 = self._행들(근거DB, 21)
        assert [tuple(r[:3]) for r in 행] == [("ID", "2100000282622", "21828")]

    def test_일련번호가_맞으면_그것으로(self, 근거DB):
        assert [tuple(r[:3]) for r in self._행들(근거DB, 22)] == [("일련번호", "2100000282622", "21828")]

    def test_이름은_후보이고_후보마다_행이다(self, 근거DB):
        행 = self._행들(근거DB, 23)
        assert [r[0] for r in 행] == ["이름후보", "이름후보"]
        assert {r[2] for r in 행} == {"10", "11"}            # 현행 'N' 인 12 는 후보가 아니다
        assert all(r[3] == 2 for r in 행)
        assert {r[4] for r in 행} == {"A부", "B부"}

    def test_미해결도_남는다(self, 근거DB):
        행 = self._행들(근거DB, 24)
        assert len(행) == 1 and 행[0][0] is None and 행[0][2] is None

    def test_ID_가_풀린_행은_이름후보를_더_내지_않는다(self, 근거DB):
        assert 근거DB.execute(
            "SELECT COUNT(*) FROM 행정규칙근거 WHERE 조문번호 IN (21, 22)").fetchone()[0] == 2

    def test_뷰_주석이_이름후보를_확정으로_읽지_말라고_말한다(self):
        ddl = re.search(r"CREATE VIEW 행정규칙근거 AS.*?;\n", 스키마.SCHEMA, re.S).group(0)
        assert "후보" in ddl and "제1조" in ddl
        assert "DISTINCT" not in ddl.split("SELECT")[0]


class Test이름과_주석:
    def test_조문판단수의_컬럼은_참조수다(self, conn):
        컬럼 = [r[1] for r in conn.execute("PRAGMA table_info(조문판단수)")]
        assert "참조수" in 컬럼 and "판단수" not in 컬럼

    def test_한_번도_판단받지_않은_은_스키마_어디에도_없다(self):
        assert "한 번도 판단받지" not in 스키마.SCHEMA and "한 번도 시험받지" not in 스키마.SCHEMA

    def test_조문판단수_첫_줄이_참조_매칭을_말한다(self):
        ddl = re.search(r"CREATE VIEW 조문판단수 AS\n(.*?)\n", 스키마.SCHEMA).group(1)
        assert "참조" in ddl and "판단 부재" in ddl or "판단받지" not in ddl

    def test_위임대상조문_주석이_가지를_말한다(self):
        ddl = re.search(r"CREATE VIEW 위임대상조문 AS.*?;\n", 스키마.SCHEMA, re.S).group(0)
        assert "조문가지번호=0" in ddl or "조문가지번호 = 0" in ddl


class Test보고값:
    def test_해결률_보고값이_네_갈래를_센다(self, 근거DB):
        _, 보, _ = 감사.run(근거DB)
        값 = dict((번호, 값) for 번호, _, 값 in 보).get("R27")
        assert 값 is not None
        for 말 in ("ID 1", "일련번호 1", "이름후보 1", "미해결 1"):
            assert 말 in str(값), 값


class Test락:
    """수집이 도는 중에 손으로 `init`·`migrate` 를 돌리면 스키마 적용이 겹친다 — 되돌릴 수 없다."""

    @pytest.mark.parametrize("명령", ["init", "migrate"])
    def test_락이_잡혀_있으면_물러난다(self, conn, db_path, 명령):
        conn.close()
        with 연결.락(db_path) as lk:
            assert lk.잡음
            r = subprocess.run([sys.executable, str(SCRIPTS / "load.py"), 명령, "--db", str(db_path)],
                               capture_output=True, text=True, timeout=120)
        assert r.returncode == 3, r.stderr[-300:]


class Test해결의_경계:
    """리뷰(2026-09-11 a6-반증)가 재현한 셋 — 인증 실패 삼킴 · '없음' 이 로컬 복구·--전량까지 막음 · ID 경로 다중 판본."""

    def test_인증_실패는_삼키지_않고_올린다(self, 위임DB):
        class 인증깨짐:
            워커 = 2
            def 본문하나(self, *a, **k):
                raise 원천.인증실패("bad key")
        with pytest.raises(원천.인증실패):
            위임.해결(위임DB, 인증깨짐(), 로그=lambda _: None)

    def test_없음_기록이_있어도_로컬에_생기면_호출_없이_채운다(self, 위임DB):
        위임.해결(위임DB, 가짜클라이언트({"2100000061069": "21828"}), 로그=lambda _: None)   # 2100000000001 → 없음
        규칙(위임DB, "2100000000001", "77", "돌아온 규칙")
        c = 가짜클라이언트({})
        위임.해결(위임DB, c, 로그=lambda _: None)
        assert c.호출 == []
        assert 위임DB.execute("SELECT 대상행정규칙ID FROM 위임 WHERE 순서=2").fetchone()[0] == "77"

    def test_전량이면_없음도_다시_묻는다(self, 위임DB):
        위임.해결(위임DB, 가짜클라이언트({"2100000061069": "21828"}), 로그=lambda _: None)
        c = 가짜클라이언트({"2100000000001": "77"})
        위임.해결(위임DB, c, 로그=lambda _: None, 전량=True)
        assert "2100000000001" in c.호출
        assert 위임DB.execute("SELECT 대상행정규칙ID FROM 위임 WHERE 순서=2").fetchone()[0] == "77"

    def test_ID_경로도_현행_판본이_여럿이면_후보수가_그_수다(self, 근거DB):
        규칙(근거DB, "2100000282623", "21828", "전자금융감독규정")          # 같은 ID 의 현행 판본 하나 더(실물 30개 ID 가 그렇다)
        행 = 근거DB.execute("SELECT 해결, 후보수 FROM 행정규칙근거 WHERE 조문번호=21").fetchall()
        assert len(행) == 2 and all(tuple(r) == ("ID", 2) for r in 행)
