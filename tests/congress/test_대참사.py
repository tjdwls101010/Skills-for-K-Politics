"""감사가 대참사를 잡는가 — **현재 DB 를 자기 자신과 비교해서는 못 잡는다.**

⚠️ 실측(2026-08-28, 이 축을 넣기 전): 국회 감사 13개 중 **12개가 빈 DB 에서 초록**이고,
의안 한 건만 남기면 **13개 전부 초록**이다. 법령도 23개 중 21개가 빈 DB 에서 초록이다.
게이트가 전부 "현재 DB 안에서 등식이 성립하는가"를 묻기 때문인데, **행이 사라지면 그
등식의 양변이 함께 사라져 오히려 잘 맞는다.**

게이트를 더 다는 것은 이 성질을 안 바꾼다. 축을 하나 넣는다 — *감사는 직전 정상과
비교한다.* 그리고 그 '직전 정상' 은 **DB 밖에** 있어야 한다. 안에 두면 DB 를 통째로
갈아치운 사고에서 기준선도 같이 사라진다. 이 레포에서 DB 밖에 사는 유일한 정상은
백업 세대의 곁기록(`기록["행수"]`)이다.
"""

import sqlite3

import pytest

import audit
import db as dbmod

import 백업픽스처


@pytest.fixture
def 백업마당(tmp_path, monkeypatch, db_path):
    보관 = tmp_path / "백업"
    보관.mkdir()
    monkeypatch.setenv("CORPUS_BACKUP_DIR", str(보관))
    return lambda **kw: 백업픽스처.세대만들기("congress", db_path, 보관, **kw)


def _채운다(conn, 의안수=50):
    """핵심표 다섯을 전부 채운다 — 하나만 채우면 나머지가 0이라 가드가 헛짚는다.

    ⚠️ **뷰가 비지 않게 하는 것까지가 "채운다"이다.** A14 는 뷰가 조회되고 비지 않는지를
    보는데, `회의의안`·`발의자` 를 빼면 `의안회의`·`법률안제안주체` 가 빈 채로 남아
    **아무것도 안 망가진 DB 가 빨갛다.**
    """
    conn.execute("INSERT INTO 의원 (의원코드, 이름) VALUES ('A1','의원A1')")
    conn.execute("INSERT INTO 위원회 (위원회명) VALUES ('정무위원회')")
    for i in range(의안수):
        번호 = f"22{i:05d}"
        conn.execute(
            "INSERT INTO 의안 (의안번호, 의안ID, 의안명, 의안종류, 제안자구분, 소관위원회)"
            " VALUES (?,?,?,'법률안','의원','정무위원회')",
            (번호, f"PRC_{i}", f"법률안{i}"))
        conn.execute(
            "INSERT INTO 발의자 (의안번호, 의원코드, 역할) VALUES (?,'A1','대표발의')", (번호,))
        conn.execute(
            "INSERT INTO 회의 (회의id, 위원회명, 회의일자, 회의종류)"
            " VALUES (?,'정무위원회','2026-01-01','상임위원회')", (i,))
        conn.execute(
            "INSERT INTO 회의의안 (회의id, 의안번호) VALUES (?,?)", (i, 번호))
        conn.execute(
            "INSERT INTO 발언 (회의id, 순서, 발언자명, 내용) VALUES (?,1,'홍길동','ㅇ')", (i,))
        conn.execute(
            "INSERT INTO 표결집계 (의안번호, 의결일) VALUES (?, '2026-01-01')", (번호,))
        conn.execute(
            "INSERT INTO 표결 (의안번호, 의원코드, 표결결과) VALUES (?,'A1','찬성')", (번호,))
    conn.commit()


def _게이트(conn, 코드):
    게, _, _ = audit.run(conn)
    return next(v for 번호, _, v, _ in 게 if 번호 == 코드)


def _초록수(conn):
    게, _, _ = audit.run(conn)
    return sum(1 for *_, ok in 게 if ok), len(게)


class Test축이_없으면_대참사가_초록이다:
    """이 클래스는 **축이 잡아야 할 것을 이름으로 못박는다.** 하나라도 통과하면
    나머지 게이트만으로는 대참사를 못 본다는 뜻이다."""

    def test_한_건만_남은_DB_에서_빨간_것이_나온다(self, conn, db_path, 백업마당):
        _채운다(conn)
        conn.close()
        백업마당()                      # 이 시점이 '직전 정상' 이다
        c = dbmod.connect(db_path)
        for t in ("표결", "표결집계", "발언", "회의", "의안"):
            c.execute(f"DELETE FROM {t}")
        c.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명)"
                  " VALUES ('2200001','PRC_x','남은 한 건')")
        c.commit()
        초록, 전체 = _초록수(c)
        assert 초록 < 전체, "의안 한 건만 남은 DB 에서 게이트가 전부 초록이다"
        c.close()

    def test_빈_DB_에서_빨간_것이_나온다(self, conn, db_path, 백업마당):
        _채운다(conn)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        for t in ("표결", "표결집계", "발언", "회의", "의안", "의원"):
            c.execute(f"DELETE FROM {t}")
        c.commit()
        초록, 전체 = _초록수(c)
        assert 초록 < 전체
        c.close()


class Test직전정상과_비교한다:
    def test_기준선이_없으면_0이다(self, conn):
        """⚠️ **기준선 없음은 위반이 아니다.** 첫 백업 전에도 수집은 돌아야 한다 —
        여기서 빨개지면 가드가 스스로를 영구화한다."""
        _채운다(conn)
        assert _게이트(conn, "A13") == 0

    def test_늘어난_것은_통과한다(self, conn, db_path, 백업마당):
        _채운다(conn, 의안수=50)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명)"
                  " VALUES ('2299999','PRC_new','새 법률안')")
        c.commit()
        assert _게이트(c, "A13") == 0
        c.close()

    def test_조금_줄어드는_것은_통과한다(self, conn, db_path, 백업마당):
        """원천이 실제로 거둬 가는 일이 있다 — 실측으로 판례가 하루 0.9% 줄었다.
        그 폭을 막으면 **고칠 수 없는 빨간불**이 되고, 그러면 표 전체를 안 보게 된다."""
        _채운다(conn, 의안수=100)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("DELETE FROM 표결 WHERE 의안번호 IN"
                  " (SELECT 의안번호 FROM 의안 LIMIT 5)")   # 5%
        c.commit()
        assert _게이트(c, "A13") == 0
        c.close()

    def test_반토막은_잡는다(self, conn, db_path, 백업마당):
        _채운다(conn, 의안수=100)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("DELETE FROM 발언 WHERE 회의id < 50")
        c.commit()
        assert _게이트(c, "A13") == 1, "핵심표 하나가 반토막인데 못 잡았다"
        c.close()

    def test_여러_표가_줄면_전부_센다(self, conn, db_path, 백업마당):
        _채운다(conn, 의안수=100)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("DELETE FROM 발언")
        c.execute("DELETE FROM 표결")
        c.commit()
        assert _게이트(c, "A13") == 2
        c.close()

    def test_왜_빨간지가_보고값에_있다(self, conn, db_path, 백업마당):
        """게이트는 수만 준다. **어느 표가 얼마나 줄었는지**가 없으면 사람이
        빨간불 앞에서 할 수 있는 일이 없다."""
        _채운다(conn, 의안수=100)
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("DELETE FROM 발언")
        c.commit()
        _, 보, _ = audit.run(c)
        값 = next(v for 번호, _, v in 보 if 번호 == "R15")
        assert "발언" in str(값) and "100" in str(값)
        c.close()

    def test_기준선이_어디서_왔는지_보고값이_밝힌다(self, conn, 백업마당):
        """⚠️ **기준선이 없어서 0인 것과 견줘 봐서 0인 것은 다른 사건이다.**
        구별이 안 되면 백업이 멈춘 날 감사가 조용히 아무것도 안 지킨다."""
        _채운다(conn)
        _, 보, _ = audit.run(conn)
        값 = str(next(v for 번호, _, v in 보 if 번호 == "R15"))
        assert "기준선 없음" in 값

    def test_국회에는_원장_예외가_없다(self, conn, db_path, 백업마당):
        """⚠️ **원장 예외는 실측 오탐이 나온 코퍼스에만 준다.** 법령이 `정리후보`·
        `수집실패` 로 매일 빨갰던 것과 달리 국회 `수집실패` 는 672 로 안정적이고
        `수집상태` 는 3행뿐이라 한 행만 사라져도 33% 다 — 그건 진짜 신호다.
        여기가 초록이 되는 날은 누군가 대칭이 예뻐 보인다는 이유로 국회에도 예외를
        준 날이고, 그 순간 실패 원장이 통째로 날아가도 아무 데서도 안 빨개진다.
        """
        _채운다(conn, 의안수=100)
        conn.execute(
            "INSERT INTO 수집실패 (대상종류, 대상키, 실패종류)"
            " SELECT '의안상세', 의안번호, '없음' FROM 의안")
        conn.commit()
        conn.close()
        백업마당()
        c = dbmod.connect(db_path)
        c.execute("DELETE FROM 수집실패")
        c.commit()
        assert _게이트(c, "A13") == 1
        c.close()


# ── 판정이 밖으로 나가는 모양 ────────────────────────────────────────────────


class Test감사만_보는_호출도_판정을_낸다:
    """⚠️ **`--audit-only` 가 빨간 게이트에도 0을 냈다.** 그래서 "무엇이 밀렸나"만 보려는
    자동화가 빨간 DB 를 초록으로 읽었다 — 게이트를 아무리 촘촘히 달아도 그 답을 아무도
    안 듣는다면 게이트가 없는 것과 같다."""

    def test_게이트가_빨가면_1로_나간다(self, conn, db_path, monkeypatch):
        import collect
        conn.execute("INSERT INTO 수집실패 (대상종류, 대상키, 실패종류)"
                     " VALUES ('의안상세','2200001','막힘')")     # A4 를 빨갛게
        conn.commit()
        monkeypatch.setattr("sys.argv", ["collect.py", "--db", str(db_path), "--audit-only"])
        assert collect._main() == 1

    def test_게이트가_초록이면_0으로_나간다(self, conn, db_path, monkeypatch):
        import collect
        _채운다(conn)
        monkeypatch.setattr("sys.argv", ["collect.py", "--db", str(db_path), "--audit-only"])
        assert collect._main() == 0


class Test감사판정이_DB_에_남는다:
    """⚠️ **세션 시작 훅은 감사를 돌릴 수 없다** — 게이트 하나가 백만 행을 훑으므로
    ms 단위 규율을 깬다. 수집기가 판정을 한 줄로 남겨야 훅이 그걸 읽는다."""

    def test_수집이_끝나면_판정이_남는다(self, conn, db_path, monkeypatch):
        import collect
        import net

        class 빈원천:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *e): pass
            def close(self): pass
            def all_pages(self, api, **kw): return []
            def json(self, api, **kw): return [], 0

        conn.close()
        monkeypatch.setattr(net, "Client", 빈원천)
        monkeypatch.setattr("sys.argv", ["collect.py", "--db", str(db_path)])
        collect._main()
        c = dbmod.connect(db_path)
        r = c.execute(
            "SELECT 건수 FROM 수집상태 WHERE 대상='감사' AND 키='전체'").fetchone()
        assert r is not None, "수집이 끝났는데 감사 판정이 아무 데도 안 남았다"
        c.close()
