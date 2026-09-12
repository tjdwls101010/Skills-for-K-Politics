"""`snapshot.build` — 원천 둘과 소관기관을 읽기 전용 통합 스냅샷으로 접는다.

이 파일이 재는 것은 **조회하는 쪽이 실제로 여는 파일의 계약**이다: 표가 다 왔는가, 행이 안
샜는가, 주석이 남았는가, 검증에 걸리면 어제 파일이 살아남는가.
"""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import snapshot

SCRIPTS = Path(snapshot.__file__).resolve().parent


class Test표와뷰가다온다:
    def test_원천_표가_전부_온다(self, 빌드):
        _, conn, _ = 빌드
        있는것 = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        for t in snapshot.국회표 + snapshot.법령표:
            assert t in 있는것, t
        assert {"신선도", "수집실패", "법률안대상법령", "소관기관"} <= 있는것

    def test_접는_표는_안_온다(self, 빌드):
        """`수집상태`·`메타` 는 수집기의 운영 원장이라 `신선도` 한 표로 접힌다."""
        _, conn, _ = 빌드
        있는것 = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert "수집상태" not in 있는것
        assert "메타" not in 있는것

    def test_원천_뷰가_전부_온다(self, 빌드):
        _, conn, _ = 빌드
        뷰 = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='view'")}
        assert {"법률안제안주체", "의안회의"} <= 뷰            # 국회
        assert {"현행법령", "현행조문", "의율판단", "조문판단수",
                "위임대상조문", "행정규칙근거", "조문개정비교",
                "현행행정규칙", "현행법령부칙", "시행대기법령", "시행예정법령"} <= 뷰   # 법령
        assert {"법률안현행조문", "의원사건"} <= 뷰            # 새로 세운 것

    def test_인덱스가_전부_온다(self, 빌드, 원천국회, 원천법령):
        _, conn, _ = 빌드
        받은것 = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        for 경로 in (원천국회, 원천법령):
            src = sqlite3.connect(f"file:{경로}?mode=ro", uri=True)
            원것 = {r[0] for r in src.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
            src.close()
            # 접는 표에 딸린 인덱스는 안 온다.
            assert 원것 - 받은것 == set() or all(
                "수집" in n or "메타" in n for n in 원것 - 받은것), 원것 - 받은것

    def test_뷰가_실제로_돈다(self, 빌드):
        """`sqlite_master` 에 있는 것과 도는 것은 다르다 — 참조하는 표가 빠지면 조회할 때 터진다."""
        _, conn, _ = 빌드
        for (이름, ) in conn.execute("SELECT name FROM sqlite_master WHERE type='view'"):
            conn.execute(f'SELECT * FROM "{이름}" LIMIT 1').fetchall()


class Test행이안샌다:
    def test_표별_행_수가_원천과_같다(self, 빌드, 원천국회, 원천법령):
        결과, conn, _ = 빌드
        for 경로, 표들 in ((원천국회, snapshot.국회표), (원천법령, snapshot.법령표)):
            src = sqlite3.connect(f"file:{경로}?mode=ro", uri=True)
            for t in 표들:
                원 = src.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                받 = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                assert 원 == 받, f"{t}: 원천 {원} vs 스냅샷 {받}"
            src.close()

    def test_소관기관이_728행_그대로_온다(self, 빌드):
        _, conn, _ = 빌드
        assert conn.execute("SELECT COUNT(*) FROM 소관기관").fetchone()[0] == 728

    def test_소관기관_DDL_이_원천_그대로다(self, 빌드, 소관기관DB):
        """주석의 진실 원천은 그 DB 자신이다 — 여기 사본을 두면 둘이 갈린다."""
        _, conn, _ = 빌드
        src = sqlite3.connect(f"file:{소관기관DB}?mode=ro", uri=True)
        원 = src.execute(
            "SELECT sql FROM sqlite_master WHERE name='소관기관'").fetchone()[0]
        src.close()
        받 = conn.execute("SELECT sql FROM sqlite_master WHERE name='소관기관'").fetchone()[0]
        assert 받 == 원

    def test_integrity_와_FK_가_통과한다(self, 빌드):
        _, conn, _ = 빌드
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


class Test주석이살아있다:
    def test_표_주석이_남는다(self, 빌드):
        """`.schema` 가 이 스냅샷의 유일한 문서다. 주석이 날아가면 지도가 없어진다."""
        _, conn, _ = 빌드
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='의원'").fetchone()[0]
        assert "동명이인이 실재해 조인 키가 아니다" in ddl

    def test_수집기_관리자_좌표가_사라진다(self, 빌드):
        """`audit R7` 은 그 게이트를 아는 사람에게만 뜻이 있다. 독자는 의원실 일을 하는 클로드다."""
        _, conn, _ = 빌드
        전부 = "\n".join(r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        for 좌표 in ("audit R1", "audit R7", "audit R17", "audit R18",
                    "감사 A19", "감사 R14", "정리 유예"):
            assert 좌표 not in 전부, 좌표

    def test_좌표를_지우는_대신_무엇을_하라를_남긴다(self, 빌드):
        _, conn, _ = 빌드
        의안 = conn.execute("SELECT sql FROM sqlite_master WHERE name='의안'").fetchone()[0]
        assert "SELECT DISTINCT 처리결과" in 의안

    def test_패치_대상이_사라지면_빌드가_선다(self):
        """조용히 넘어가면 우리는 이미 없는 주석을 고치고 있다고 믿게 된다."""
        with pytest.raises(ValueError, match="주석 패치의 대상이 0번"):
            snapshot.주석적용("국회", ["CREATE TABLE x (a TEXT)"])

    def test_인위적인_줄바꿈이_없다(self, 빌드):
        """주석 한 줄은 한 생각이다. 80자에서 끊으면 `head`·`grep` 이 무엇에 대한 말인지 잃은
        조각을 집는다 — `-- COALESCE(a, b) 다` 같은 것."""
        _, conn, _ = 빌드
        for (이름, ), in [(r, ) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE name IN ('신선도','수집실패','법률안대상법령','소관기관')")]:
            ddl = conn.execute(
                "SELECT sql FROM sqlite_master WHERE name=?", (이름, )).fetchone()[0]
            줄 = [l.strip() for l in ddl.splitlines() if l.strip().startswith("--")]
            # 한 줄이 접속사·조사 없이 끝나 다음 줄로 이어지는 모양을 금지한다.
            for i, l in enumerate(줄[:-1]):
                assert not l.endswith(("—", "그", "이", "저", "및", ",")), f"{이름}[{i}]: {l}"


class Test신선도가시점을진다:
    def test_코퍼스별_한_행씩_있다(self, 빌드):
        _, conn, _ = 빌드
        assert {r[0] for r in conn.execute("SELECT 코퍼스 FROM 신선도")} == {
            "국회", "법령", "소관기관"}

    def test_국회_적재기준은_의안의_마지막_수집시각이다(self, 빌드):
        _, conn, _ = 빌드
        시각 = conn.execute(
            "SELECT 적재기준시각 FROM 신선도 WHERE 코퍼스='국회'").fetchone()[0]
        assert 시각 == "2026-09-11 03:00:00"

    def test_감사_위반이_있으면_감사통과가_0_이고_상세가_남는다(self, 빌드):
        _, conn, _ = 빌드
        통과, 상세 = conn.execute(
            "SELECT 감사통과, 감사상세 FROM 신선도 WHERE 코퍼스='국회'").fetchone()
        assert 통과 == 0 and 상세 == "A11=1"

    def test_법령은_메타에서_온다(self, 빌드):
        _, conn, _ = 빌드
        시각, 통과 = conn.execute(
            "SELECT 적재기준시각, 감사통과 FROM 신선도 WHERE 코퍼스='법령'").fetchone()
        assert 시각 == "2026-09-12 08:25:15" and 통과 == 1

    def test_빌드시각이_모든_행에_있다(self, 빌드):
        결과, conn, _ = 빌드
        시각 = {r[0] for r in conn.execute("SELECT 빌드시각 FROM 신선도")}
        assert 시각 == {결과["빌드시각"]}

    def test_건너뛴_수집은_경고_문장이_된다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        """'건너뜀' 이면 정당·현직여부가 옛 값이다. 상태 코드가 아니라 문장으로 남긴다."""
        c = sqlite3.connect(원천국회)
        c.execute("UPDATE 수집상태 SET 상태='건너뜀' WHERE 대상='의원현직'")
        c.commit()
        c.close()
        출력 = tmp_path / "법2.db"
        snapshot.build(원천국회, 원천법령, 소관기관DB, 출력)
        conn = sqlite3.connect(f"file:{출력}?mode=ro", uri=True)
        경고 = conn.execute("SELECT 경고 FROM 신선도 WHERE 코퍼스='국회'").fetchone()[0]
        conn.close()
        assert "의원현직" in 경고 and "옛 값" in 경고


class Test수집실패가통합된다:
    def test_두_원장이_한_표에_들어온다(self, 빌드):
        _, conn, _ = 빌드
        행 = dict(conn.execute("SELECT 코퍼스, COUNT(*) FROM 수집실패 GROUP BY 1"))
        assert 행 == {"국회": 1, "법령": 1}

    def test_법령쪽_컬럼이름이_맞춰진다(self, 빌드):
        _, conn, _ = 빌드
        assert conn.execute(
            "SELECT 대상종류, 대상키, 상세, 마지막시도 FROM 수집실패 WHERE 코퍼스='법령'"
        ).fetchone() == ("판례", "99999", "원천 404", "2026-09-12 08:10:00")


class Test교차뷰:
    def test_법률안현행조문이_법안과_현행조문을_잇는다(self, 빌드):
        _, conn, _ = 빌드
        행 = conn.execute(
            "SELECT 의안명, 법령명, 법령일련번호, 조문번호 FROM 법률안현행조문"
            " WHERE 의안번호='2200001'").fetchall()
        assert 행 == [("개인정보 보호법 일부개정법률안", "개인정보 보호법", "100001", 34)]

    def test_의원사건이_발의_표결_발언을_한_축에_놓는다(self, 빌드):
        _, conn, _ = 빌드
        종류 = [r[0] for r in conn.execute(
            "SELECT 종류 FROM 의원사건 WHERE 의원코드='KBZ59750' ORDER BY 일자")]
        assert sorted(종류) == ["발언", "발의", "표결"]

    def test_의원사건은_의원코드를_못_푼_발언을_뺀다(self, 빌드):
        _, conn, _ = 빌드
        assert conn.execute(
            "SELECT COUNT(*) FROM 의원사건 WHERE 종류='발언'").fetchone()[0] == 1


class Test락:
    def test_수집기가_락을_쥐고_있으면_rc_3(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        """존재 여부가 아니라 실제 `flock` 으로 판단한다 — 수집기는 락 파일을 지우지 않고 닫는다."""
        import fcntl
        락 = Path(str(원천국회) + ".lock")
        락.write_text("1234")
        with 락.open("a+") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            r = subprocess.run(
                [sys.executable, str(SCRIPTS / "snapshot.py"),
                 "--원천국회", str(원천국회), "--원천법령", str(원천법령),
                 "--소관기관", str(소관기관DB), "--출력", str(tmp_path / "법.db"),
                 "--락타임아웃", "1"],
                capture_output=True, text=True, timeout=120)
        assert r.returncode == 3, r.stdout + r.stderr
        assert not (tmp_path / "법.db").exists()

    def test_남아있는_락_파일만으로는_물러나지_않는다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        """존재로 판단하면 첫 수집 이후 영영 못 돈다."""
        Path(str(원천국회) + ".lock").write_text("1234")
        Path(str(원천법령) + ".lock").write_text("5678")
        결과 = snapshot.build(원천국회, 원천법령, 소관기관DB, tmp_path / "법.db", 락타임아웃=1)
        assert 결과["행수"]["의안"] == 3


class Test실패하면어제것이산다:
    def test_행_수가_어긋나면_출력을_안_바꾼다(self, tmp_path, 원천국회, 원천법령,
                                          소관기관DB, monkeypatch):
        출력 = tmp_path / "법.db"
        출력.write_bytes("어제것".encode())
        진짜 = snapshot._표복사

        def 한표를_흘린다(대상, 별칭, 표들):
            수 = 진짜(대상, 별칭, 표들)
            if "의안" in 수:
                대상.execute("DELETE FROM 의안 WHERE 의안번호='2200003'")
                수["의안"] -= 1
            return 수

        monkeypatch.setattr(snapshot, "_표복사", 한표를_흘린다)
        with pytest.raises(RuntimeError, match="행 수"):
            snapshot.build(원천국회, 원천법령, 소관기관DB, 출력)
        assert 출력.read_bytes() == "어제것".encode()

    def test_중간_상태가_노출되지_않는다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        """검증을 다 통과한 뒤 `os.replace` 한 판본만 그 이름을 가진다."""
        출력 = tmp_path / "법.db"
        snapshot.build(원천국회, 원천법령, 소관기관DB, 출력)
        assert not list(tmp_path.glob("법.db.빌드중-*"))

    def test_원천이_없으면_유령_DB_를_안_만든다(self, tmp_path, 원천법령, 소관기관DB):
        없는것 = tmp_path / "없다" / "국회.db"
        with pytest.raises(FileNotFoundError):
            snapshot.build(없는것, 원천법령, 소관기관DB, tmp_path / "법.db")
        assert not 없는것.exists()
        assert not (tmp_path / "법.db").exists()


class TestCLI:
    def test_stdout_이_JSON_한_줄이다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        """워크플로가 이 줄을 읽는다 — 사람이 읽는 로그는 stderr 쪽이다."""
        import json
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "snapshot.py"),
             "--원천국회", str(원천국회), "--원천법령", str(원천법령),
             "--소관기관", str(소관기관DB), "--출력", str(tmp_path / "법.db")],
            capture_output=True, text=True, timeout=300)
        assert r.returncode == 0, r.stdout + r.stderr
        결과 = json.loads(r.stdout.strip())
        assert 결과["행수"]["의안"] == 3
        assert 결과["매칭"]["법률안"] == 3
        assert 결과["크기"] > 0

    def test_help_가_뜬다(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "snapshot.py"), "--help"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0
        assert "rc 3" in r.stdout


class Test주석에건수가없다:
    """건수는 이 DB 에서 **쓰는 순간부터 낡는다** — 수집이 하루 1회 돌기 때문이다.

    수집기 스키마에는 이미 이 검사가 걸려 있었는데(`tests/*/test_*주석수치.py`), 스냅샷이
    새로 쓰는 주석과 주석 패치는 그 검사 밖이었다. 같은 DB 를 설명하는 두 표면 중 검사가
    걸린 쪽만 깨끗해지는 것이 정확히 이 레포에서 한 번 일어난 일이다.
    """

    def test_스냅샷_스키마_전체에_실측_건수가_없다(self, 빌드):
        import 주석수치 as 검사기
        _, conn, _ = 빌드
        schema = "\n".join(r[0] for r in conn.execute(
            "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
        남은 = 검사기.주석수치(schema)
        assert not 남은, (
            "주석에 실측 건수·비율이 있다. **조회로 대체해라** — `SELECT … GROUP BY 1` 한 줄이"
            " 짧으면서 동시에 안 낡는다:\n"
            + "\n".join(f"  {v!r}  ←  {l[:100]}" for v, l in 남은[:10]))


class Test뷰검증의구멍:
    """`CREATE VIEW` 는 참조 표가 없어도 성공한다 — 코덱스 리뷰가 실측으로 잡은 구멍이다.

    접는 표(`수집상태`·`메타`)를 참조하는 뷰가 원천에 새로 생기면, 그 뷰는 스냅샷에 실린 채
    표별 행 수·`integrity_check`·`foreign_key_check` 를 **셋 다 통과한다.** 조회하는 쪽이
    `no such table` 을 만나는 것은 그날 스냅샷이 이미 자리를 차지한 뒤다.
    """

    def test_접는_표를_참조하는_뷰가_원천에_생기면_빌드가_선다(
            self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        c = sqlite3.connect(원천국회)
        c.execute("CREATE VIEW 수집상태요약 AS SELECT 대상, 상태 FROM 수집상태")
        c.commit()
        c.close()
        with pytest.raises(RuntimeError, match="뷰가 참조하는 표가 스냅샷에 없다"):
            snapshot.build(원천국회, 원천법령, 소관기관DB, tmp_path / "법.db")
        assert not (tmp_path / "법.db").exists()

    def test_에러가_무엇을_하라를_말한다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        c = sqlite3.connect(원천법령)
        c.execute("CREATE VIEW 메타요약 AS SELECT 키 FROM 메타")
        c.commit()
        c.close()
        with pytest.raises(RuntimeError) as e:
            snapshot.build(원천국회, 원천법령, 소관기관DB, tmp_path / "법.db")
        assert "메타요약" in str(e.value)
        assert "국회표" in str(e.value) or "법령표" in str(e.value)


class Test시행대기가섞인다:
    """원천이 '현행'으로 분류한 판본에 **시행일이 아직 안 온 것**이 섞인다.

    실측(2026-09-12)에서 `법률안현행조문` 에 다음 날 시행되는 형법 판본이 나왔다. 그래서
    이 뷰를 통째로 "지금 조문"으로 인용하면 안 되고, 주석이 그걸 말해야 한다.
    """

    def test_시행대기를_노출한다(self, 빌드):
        _, conn, _ = 빌드
        컬럼 = [r[1] for r in conn.execute("PRAGMA table_info(법률안현행조문)")]
        assert "시행대기" in 컬럼

    def test_주석이_시행대기를_거르라고_말한다(self, 빌드):
        _, conn, _ = 빌드
        ddl = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name='법률안현행조문'").fetchone()[0]
        assert "시행대기 = 0" in ddl
        assert "여기 없는 법률안이 두 종류다" in ddl


class Test감사시각:
    """감사는 **수집 파이프라인이 돌 때만** 원장에 기록된다. `audit.py` 를 손으로 돌려도 안 쓴다.

    그래서 `감사통과=0` 만 보여 주면 8일 전 실패가 오늘 실패처럼 읽힌다 — 실측에서 정확히
    그랬다(원장은 09-04 의 `A11=1`, 그날 실제 감사는 15/15 초록).
    """

    def test_감사시각을_담는다(self, 빌드):
        _, conn, _ = 빌드
        시각 = conn.execute(
            "SELECT 감사시각 FROM 신선도 WHERE 코퍼스='국회'").fetchone()[0]
        assert 시각 == "2026-09-04 06:45:59"

    def test_법령은_메타의_마지막감사일시를_쓴다(self, tmp_path, 원천국회, 원천법령, 소관기관DB):
        c = sqlite3.connect(원천법령)
        c.execute("INSERT INTO 메타 VALUES ('마지막감사일시','2026-09-12 08:27:57')")
        c.commit()
        c.close()
        출력 = tmp_path / "법3.db"
        snapshot.build(원천국회, 원천법령, 소관기관DB, 출력)
        conn = sqlite3.connect(f"file:{출력}?mode=ro", uri=True)
        assert conn.execute(
            "SELECT 감사시각 FROM 신선도 WHERE 코퍼스='법령'").fetchone()[0] == "2026-09-12 08:27:57"
        conn.close()

    def test_주석이_적재기준시각과의_간극을_말한다(self, 빌드):
        _, conn, _ = 빌드
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name='신선도'").fetchone()[0]
        assert "적재기준시각" in ddl.split("감사시각")[1].split("감사상세")[0]
