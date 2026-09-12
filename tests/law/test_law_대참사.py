"""법령 감사가 대참사를 잡는가 — 국회 쪽 `tests/congress/test_대참사.py` 와 같은 축이다.

⚠️ 실측(2026-08-28, 축을 넣기 전): **빈 DB 에서 23개 중 21개가 초록**이었다. 게이트가 전부
"현재 DB 안에서 등식이 성립하는가"를 묻는데, 행이 사라지면 그 등식의 양변이 함께 사라져
**오히려 잘 맞기 때문이다.** 여기 잡히는 것은 그 성질 자체이지 개별 게이트의 빈틈이 아니다.
"""

import pytest

from law import audit as 감사
from law.collectors import run as 실행


import 백업픽스처


@pytest.fixture
def 백업마당(tmp_path, monkeypatch, db_path):
    보관 = tmp_path / "backup"
    보관.mkdir()
    monkeypatch.setenv("CORPUS_BACKUP_DIR", str(보관))
    return lambda **kw: 백업픽스처.세대만들기("law", db_path, 보관, **kw)


def _채운다(conn, 건수=100):
    """핵심표 다섯을 전부 채운다 — 하나만 채우면 나머지가 0이라 가드가 헛짚는다."""
    때 = "2026-08-28 00:00:00"
    for i in range(건수):
        conn.execute(
            "INSERT INTO 법령 (법령ID, 법령일련번호, 법령명, 법종구분, 시행일자, 수집일시)"
            " VALUES (?,?,?, '법률', '2020-01-01', ?)", (i, i, f"법률{i}", 때))
        conn.execute(
            "INSERT INTO 조문 (법령ID, 순서, 조, 가지, 전문) VALUES (?,0,?,0,'내용')", (i, i))
        conn.execute("INSERT INTO 판례 (판례ID, 사건번호, 수집일시) VALUES (?,?,?)",
                     (i, f"2020다{i}", 때))
        conn.execute(
            "INSERT INTO 헌재결정례 (헌재결정례ID, 사건번호, 전문, 수집일시) VALUES (?,?,'전문',?)",
            (i, f"2020헌바{i}", 때))
        conn.execute(
            "INSERT INTO 법령해석례 (법령해석례ID, 안건번호, 안건명, 수집일시) VALUES (?,?,?,?)",
            (i, f"20-{i:04d}", f"해석{i}", 때))
    conn.commit()


def _게이트(conn, 코드):
    게, _, _ = 감사.run(conn)
    return next(v for 번호, _, v, _ in 게 if 번호 == 코드)


class Test축이_없으면_대참사가_초록이다:
    def test_한_건만_남은_DB_에서_빨간_것이_나온다(self, conn, db_path, 백업마당):
        _채운다(conn)
        conn.commit()
        백업마당()
        for t in ("조문", "판례", "헌재결정례", "법령해석례"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM 법령 WHERE 법령일련번호 <> 0")
        conn.commit()
        게, _, _ = 감사.run(conn)
        assert sum(1 for *_, ok in 게 if ok) < len(게)
        assert _게이트(conn, "A27") > 0, "직전 정상과 견주는 축이 이걸 못 잡으면 축이 아니다"


class Test직전정상과_비교한다:
    def test_기준선이_없으면_0이다(self, conn):
        _채운다(conn)
        assert _게이트(conn, "A27") == 0

    def test_조금_줄어드는_것은_통과한다(self, conn, db_path, 백업마당):
        """실측으로 원천이 하루 만에 판례를 0.9% 거둬 갔다. 그 폭을 막으면
        **고칠 수 없는 빨간불**이 되고, 그러면 표 전체를 안 보게 된다."""
        _채운다(conn, 건수=100)
        conn.commit()
        백업마당()
        # ⚠️ 일련번호는 TEXT 다 — `< 5` 는 문자열 비교라 '10'·'49' 까지 걸린다(실측 45행).
        conn.execute("DELETE FROM 판례 WHERE 판례ID IN"
                     " (SELECT 판례ID FROM 판례 LIMIT 5)")   # 5%
        conn.commit()
        assert _게이트(conn, "A27") == 0

    def test_반토막은_잡는다(self, conn, db_path, 백업마당):
        _채운다(conn, 건수=100)
        conn.commit()
        백업마당()
        conn.execute("DELETE FROM 판례 WHERE 판례ID IN"
                     " (SELECT 판례ID FROM 판례 LIMIT 50)")
        conn.commit()
        assert _게이트(conn, "A27") == 1

    def test_왜_빨간지가_보고값에_있다(self, conn, db_path, 백업마당):
        _채운다(conn, 건수=100)
        conn.commit()
        백업마당()
        conn.execute("DELETE FROM 판례")
        conn.commit()
        _, 보, _ = 감사.run(conn)
        값 = str(next(v for 번호, _, v in 보 if 번호 == "R24"))
        assert "판례" in 값 and "100" in 값

    def test_기준선이_어디서_왔는지_보고값이_밝힌다(self, conn, 백업마당):
        """⚠️ **기준선이 없어서 0인 것과 견줘 봐서 0인 것은 다른 사건이다.**
        구별이 안 되면 백업이 멈춘 날 감사가 조용히 아무것도 안 지킨다."""
        _채운다(conn)
        _, 보, _ = 감사.run(conn)
        assert "기준선 없음" in str(next(v for 번호, _, v in 보 if 번호 == "R24"))


class Test원장이_줄어든_것은_사고가_아니다:
    """⚠️ **원장은 일이 끝나면 줄어드는 표다.** 그것을 급감으로 세면 **가장 잘 돌아간
    날이 가장 빨갛다** — 실측으로 `수집실패 507→1` 이 A27 을 빨갛게 만들었고, 정작
    핵심표(법령 6,425 · 조문 310,021 · 판례 89,312)는 평평했다.

    ⚠️ **그렇다고 비교에서 빼지는 않는다.** 곁기록의 `행수` 는 감사가 DB 밖에서 잡는
    유일한 축이라, 목록에서 지우면 빨강만이 아니라 **상세에서도 사라져** 그 표에 대해
    축이 통째로 꺼진다. 판정에서만 빼고 보고에는 남긴다.
    """

    def _원장을_채운다(self, conn, 건수):
        conn.executemany(
            "INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 시도횟수, 최초일시, 최종일시)"
            " VALUES ('법령', ?, '목록누락', 1, 'x', 'x')",
            [(f"P{i}",) for i in range(건수)])
        conn.commit()

    def test_원장이_줄어도_A27_은_초록이다(self, conn, db_path, 백업마당):
        _채운다(conn)
        self._원장을_채운다(conn, 100)
        백업마당()
        conn.execute("DELETE FROM 수집실패")
        conn.commit()
        assert _게이트(conn, "A27") == 0

    def test_그래도_R24_에는_원장이라고_적혀_남는다(self, conn, db_path, 백업마당):
        _채운다(conn)
        self._원장을_채운다(conn, 100)
        백업마당()
        conn.execute("DELETE FROM 수집실패")
        conn.commit()
        _, 보, _ = 감사.run(conn)
        값 = str(next(v for 번호, _, v in 보 if 번호 == "R24"))
        assert "수집실패" in 값, "판정에서 뺐다고 눈에서도 없애면 축이 꺼진 것이다"
        assert "원장" in 값, "판정 대상이 아니라는 표시가 없으면 사람이 빨강으로 읽는다"

    def test_원장이_아닌_표는_그대로_빨갛다(self, conn, db_path, 백업마당):
        """원장 제외가 다른 표까지 헐겁게 만들지 않는다는 것을 같은 판에서 확인한다."""
        _채운다(conn)
        self._원장을_채운다(conn, 100)
        백업마당()
        conn.execute("DELETE FROM 수집실패")
        conn.execute("DELETE FROM 판례")
        conn.commit()
        assert _게이트(conn, "A27") == 1


# ── 판정이 DB 에 남는다 ──────────────────────────────────────────────────────


class Test감사판정이_DB_에_남는다:
    """⚠️ **세션 시작 훅은 감사를 돌릴 수 없다** — 게이트 하나가 30만 행을 훑으므로
    ms 단위 규율을 깬다. 그런데 나이만 보면 **게이트가 빨간 실행이 고친 DB 도 '최신'**
    이다(실측 2026-08-27: A1 위반으로 실패했는데 `마지막수집일시` 는 이미 갱신돼 있었다).
    수집기가 판정을 한 줄로 남겨야 훅이 그걸 읽는다."""

    def test_감사만_돌려도_판정이_남는다(self, conn, db_path):
        _채운다(conn, 건수=3)
        conn.commit()
        assert 실행._main(["--감사만", "--db", str(db_path)]) in (0, 1)
        행 = conn.execute(
            "SELECT 상태, 건수 FROM 수집상태 WHERE 자료종류='전체' AND 단계='감사'").fetchone()
        assert 행 is not None, "감사가 돌았는데 판정이 아무 데도 안 남았다"
        assert 행[0] == "완료" and 행[1] is not None

    def test_빨간_게이트는_위반_건수와_이름으로_남는다(self, conn, db_path):
        """⚠️ **상세는 게이트 번호가 아니라 이름이다.** `.schema` 만 읽는 조회자가
        'A3=1' 을 풀 수단이 없다 — 그러면 `신선도.감사상세` 가 "뭔가 어긋났다"까지만 말한다."""
        _채운다(conn, 건수=3)
        conn.execute("INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 최초일시, 최종일시)"
                     " VALUES ('판례','1','재시도','2026-08-28 00:00:00','2026-08-28 00:00:00')")
        conn.commit()

        실행._main(["--감사만", "--db", str(db_path)])
        상태, 건수, 상세 = conn.execute(
            "SELECT 상태, 건수, 상세 FROM 수집상태"
            " WHERE 자료종류='전체' AND 단계='감사'").fetchone()
        assert (상태, 건수 > 0) == ("완료", True)
        assert "재시도" in 상세 and "A3" not in 상세
        assert conn.execute("SELECT 감사통과 FROM 신선도").fetchone()[0] == 0
