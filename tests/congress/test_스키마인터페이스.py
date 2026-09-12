"""스키마가 모델에게 **전달되는 모양** — 텍스트 규칙 · 표 순서 · `schema` 명령 · 헤더.

실사용 세션(2026-09-07)의 첫 동작이 `.schema | head -150` 이었고, 319줄 중 뒤쪽의 `의안`·뷰를
못 봐 `no such column` 을 두 번 냈다. 모델은 긴 출력을 자른다 — 그 습성을 인터페이스가
흡수해야 한다: **목록 → 선택한 표의 정의**, 그리고 SCHEMA 가 정한 읽는 순서.
"""

import re
from datetime import datetime, timedelta

import pytest

import audit
import db

읽는순서 = [
    "의안", "의안심사", "발의자", "표결집계", "표결", "회의", "발언", "회의의안",
    "위원회", "의원", "의원위원회",
    "수집상태", "수집실패",
]
뷰들 = ["법률안제안주체", "의안회의"]


class TestSCHEMA_텍스트:
    def test_표_순서가_읽는_순서다(self):
        """조회 핵심 → 차원 → 운영. `sqlite_master.rowid` 는 재구축(`이행`)마다 바뀌므로
        순서는 상수가 갖는다."""
        assert [m.group(1) for m in db._테이블문.finditer(db.SCHEMA)] == 읽는순서

    def test_명령형_레일이_없다(self):
        """「언제나 LEFT JOIN」「언제나 COUNT(DISTINCT)」「조인 키는 언제나」— 명령은 그
        명령이 나오는 **데이터의 모양**으로 바꾼다. 모양을 알면 안 적힌 경우도 맞는다."""
        for 말 in ("언제나", "반드시"):
            assert 말 not in db.SCHEMA, f"주석에 {말!r} 가 남아 있다"

    @pytest.mark.parametrize("거짓", [
        "이 컬럼이 \"소위인가\"의 답이다",        # 상위 NULL 인 소위 이름이 실재한다
        "NULL 이면 계류다",                        # 비법률안은 그렇지 않았다(이제 채운다)
        "예외가 하나도 없다",                      # 관측이지 계약이 아니다 — R7 을 가리킨다
        "예외가 없고",
        "이 DB 로는 그 질문에 답할 수 없다",       # 「담지 않는다」
        "지금 시점의 정당",                        # 현직 API 확인값 + 정당확인일
    ])
    def test_실물과_어긋난_주장이_없다(self, 거짓):
        assert 거짓 not in db.SCHEMA

    def test_한_주장_한_줄이라_전체가_짧다(self):
        """한 주장을 2~3줄로 접은 것을 펴면 실사용의 `head -150` 안에 표 정의 전체가
        들어온다. 줄 수 자체가 목표는 아니고 잘라 읽기의 완화다 — 상한은 넉넉히 둔다."""
        assert len(db.SCHEMA.strip().splitlines()) <= 200


class Test보고값:
    def _보(self, conn):
        _, 보, _ = audit.run(conn)
        return {번호: 값 for 번호, _, 값 in 보}

    def test_R17_정당확인일이_오래된_현직을_센다(self, conn):
        """현직인데 7일 넘게 정당을 확인 못 했다 = 현직 API 가 그만큼 안 돌았거나 가드에
        막혔다. 그 사이 정당 집계는 옛 값이다."""
        옛날 = (datetime.now(db.KST) - timedelta(days=20)).strftime("%Y-%m-%d")
        오늘 = datetime.now(db.KST).strftime("%Y-%m-%d")
        conn.executemany(
            "INSERT INTO 의원 (의원코드,이름,정당,정당확인일,현직여부) VALUES (?,?,?,?,?)",
            [("A1", "가", "국민의힘", 옛날, 1), ("A2", "나", "국민의힘", 오늘, 1),
             ("A3", "다", "국민의힘", None, 1), ("A4", "라", "국민의힘", 옛날, 0)],
        )
        assert self._보(conn)["R17"] == 2       # A1(오래됨) · A3(확인 없음). 전직 A4 는 안 센다

    def test_R18_상위_없는_소위_이름을_센다(self, conn):
        """`농림축산식품법안심사소위` 같은 별칭이 상위 없이 실재한다 — `상위위원회 IS NULL`
        을 「소위 아님」으로 읽으면 그 행들이 상임위로 세어진다."""
        db.upsert_위원회(conn, "정무위원회")
        db.upsert_위원회(conn, "정무위원회 법안심사제1소위원회", 상위="정무위원회")
        db.upsert_위원회(conn, "농림축산식품법안심사소위")
        assert self._보(conn)["R18"] == 1

    def test_둘_다_보고값_목록에_있다(self):
        번호들 = {번호 for 번호, _, _ in audit.보고}
        assert {"R17", "R18"} <= 번호들


class Test적재헤더:
    """`schema` 와 `조회.py` 의 첫 줄. 답에 붙일 표지(적재 기준 시각 · 감사 게이트 상태)가
    쓰는 순간에 보인다 — SessionStart 훅은 스킬이 못 가지고, Codex 는 훅이 없다."""

    def test_기록이_없으면_없다고_말한다(self, conn):
        줄 = db.적재헤더(conn)
        assert "\n" not in 줄
        assert "없" in 줄

    def test_적재_시각과_감사_상태를_한_줄에(self, conn):
        db.upsert_위원회(conn, "정무위원회")
        conn.execute(
            "INSERT INTO 의안 (의안번호,의안ID,의안명,수집시각) VALUES ('2200001','X','x','2026-09-04 06:44:19')")
        conn.execute(
            "INSERT INTO 수집상태 (대상,키,상태,건수,상세,갱신일시) VALUES ('감사','전체','완료',1,'A11=1','2026-09-04 06:45:59')")
        줄 = db.적재헤더(conn)
        assert "2026-09-04 06:44" in 줄
        assert "A11=1" in 줄


class Test스키마명령:
    """`db.py schema` — 인자 없음 = 목록, 이름들 = 그 정의(SCHEMA 순서). 실물 DB 를 읽는다."""

    def _돌린다(self, capsys, *인자):
        코드 = db._main(["schema", *인자])
        나옴 = capsys.readouterr()
        return 코드, 나옴.out, 나옴.err

    def test_인자_없으면_표와_뷰의_목록이다(self, conn, db_path, capsys):
        conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('정무위원회')")
        conn.commit()
        코드, out, _ = self._돌린다(capsys, "--db", str(db_path))
        assert 코드 == 0
        줄들 = out.strip().splitlines()
        assert len(줄들) == 1 + len(읽는순서) + len(뷰들), out
        이름들 = [줄.split()[0] for 줄 in 줄들[1:]]
        assert 이름들 == 읽는순서 + 뷰들
        위원회줄 = next(줄 for 줄 in 줄들 if 줄.startswith("위원회"))
        assert "1" in 위원회줄 and "차원" in 위원회줄        # 행수 · DDL 첫 주석에서 뽑은 한 줄 역할
        # 컬럼 이름도 목록에 — e2e(2026-09-11 S3)에서 모델이 정의를 읽기 전에 `m.의원명` 을 짐작해
        # `no such column` 을 냈다. 이름만 목록에 있으면 그 짐작이 사라진다.
        assert "위원회명" in 위원회줄 and "상위위원회" in 위원회줄
        뷰줄 = next(줄 for 줄 in 줄들 if 줄.startswith("의안회의"))
        assert "경유의안번호" in 뷰줄 and "경로" in 뷰줄
        assert "CREATE TABLE" not in out

    def test_이름을_주면_그_정의만_SCHEMA_순서로(self, conn, db_path, capsys):
        """SQLite 는 저장할 때 `IF NOT EXISTS` 를 뗀다 — DB 의 원문을 그대로 낸다."""
        코드, out, _ = self._돌린다(capsys, "회의", "의안", "--db", str(db_path))
        assert 코드 == 0
        assert "CREATE TABLE 의안" in out
        assert "CREATE TABLE 회의" in out
        assert "CREATE TABLE 발언" not in out
        assert out.index("CREATE TABLE 의안") < out.index("CREATE TABLE 회의")

    def test_뷰도_이름으로_고른다(self, conn, db_path, capsys):
        코드, out, _ = self._돌린다(capsys, "의안회의", "--db", str(db_path))
        assert 코드 == 0 and "CREATE VIEW 의안회의" in out

    def test_모르는_이름은_목록을_알려주며_거부한다(self, conn, db_path, capsys):
        코드, out, err = self._돌린다(capsys, "발언자", "--db", str(db_path))
        assert 코드 != 0
        assert "발언" in err and "의안" in err

    def test_첫_줄이_적재헤더다(self, conn, db_path, capsys):
        for 인자 in ((), ("의안",)):
            _, out, _ = self._돌린다(capsys, *인자, "--db", str(db_path))
            assert out.splitlines()[0] == db.적재헤더(conn)

    def test_DB_것을_낸다(self, conn, db_path, capsys):
        """상수를 내면 「내가 본 스키마」와 「DB 의 스키마」가 조용히 갈린다."""
        conn.execute("PRAGMA writable_schema=ON")
        conn.execute("UPDATE sqlite_master SET sql = sql || '  -- DB 에만 있는 표시' WHERE type='table' AND name='의안'")
        conn.execute("PRAGMA writable_schema=OFF")
        conn.commit()
        _, out, err = self._돌린다(capsys, "의안", "--db", str(db_path))
        assert "DB 에만 있는 표시" in out
        assert "낡았다" in err                       # 드리프트 경고는 stderr

    def test_help_가_문서다(self, capsys):
        with pytest.raises(SystemExit):
            db._main(["schema", "--help"])
        out = capsys.readouterr().out
        assert "목록" in out and "정의" in out
