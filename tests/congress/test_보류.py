"""'보류' — 사람이 확인한 포기는 게이트에서 빠지고 보고값에 남는다.

⚠️ **고칠 수 없는 빨간불은 표 전체를 무시하게 만든다.** 회의본문 55355 는 원천이 남의
본문(57162)을 22회째 돌려주는 항목이라 사람이 할 수 있는 일이 "원장 행을 지우고 5회 더
시도"뿐이었고, 그래서 일주일마다 같은 A11 빨강이 돌아왔다(8월 #42, 9월 1~4일).
'보류' 는 사람이 **한 번 보고 표시한** 포기다 — A11 은 안 세고, R6 는 계속 보이고,
새로 상한에 닿는 항목은 여전히 빨갛다.
"""

import pytest

import audit
import collect
import db
import meetings

from test_재시도큐 import 세는클라이언트, 열거행


def 상한까지(conn, 키="55355"):
    with db.트랜잭션(conn):
        for _ in range(db.재시도상한):
            db.record_failure(conn, "회의본문", 키, "재시도", "오배송: 55355 를 요청했는데 본문은 57162 다")


def 게이트값(conn, 번호):
    게, _, _ = audit.run(conn)
    return next(v for n, _, v, _ in 게 if n == 번호)


def 보고값(conn, 번호):
    _, 보, _ = audit.run(conn)
    return next(v for n, _, v in 보 if n == 번호)


class Test표시:
    def test_상한에_닿은_재시도만_보류로_바꿀_수_있다(self, conn):
        상한까지(conn)
        db.보류(conn, "회의본문", "55355", "원천이 57162 본문을 준다")
        행 = conn.execute("SELECT 실패종류, 상세 FROM 수집실패 WHERE 대상키='55355'").fetchone()
        assert 행[0] == "보류"
        assert "원천이 57162 본문을 준다" in 행[1] and "오배송" in 행[1], "이유가 원래 상세를 지웠다"

    def test_원장에_없으면_거부한다(self, conn):
        with pytest.raises(ValueError):
            db.보류(conn, "회의본문", "55355", "이유")

    def test_아직_시도_중인_것은_거부한다(self, conn):
        """포기하지 않은 것을 사람이 먼저 포기시키면 안 된다 — 큐가 아직 풀 수 있다."""
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
        with pytest.raises(ValueError):
            db.보류(conn, "회의본문", "55355", "이유")

    def test_이유_없이는_거부한다(self, conn):
        상한까지(conn)
        with pytest.raises(ValueError):
            db.보류(conn, "회의본문", "55355", "  ")


class Test게이트와_보고값:
    def test_보류는_A11_에_안_세이고_R6_에_보인다(self, conn):
        상한까지(conn)
        assert 게이트값(conn, "A11") == 1
        db.보류(conn, "회의본문", "55355", "원천 결함")
        assert 게이트값(conn, "A11") == 0
        assert "보류 1" in 보고값(conn, "R6")

    def test_새로_상한에_닿은_것은_여전히_빨갛다(self, conn):
        상한까지(conn, "55355")
        db.보류(conn, "회의본문", "55355", "원천 결함")
        상한까지(conn, "57183")
        assert 게이트값(conn, "A11") == 1


class Test큐:
    def test_보류는_다시_묻지_않는다(self, conn):
        """큐에서도, "아직 안 받은 회의" 둘째 목록에서도 빠져야 한다 — 후자를 빼먹으면
        상한에 닿은 항목이 되돌아오던 우회로가 그대로 열린다(`db.포기한것` 참고)."""
        상한까지(conn)
        db.보류(conn, "회의본문", "55355", "원천 결함")
        c = 세는클라이언트([열거행(55355), 열거행(57001)])
        meetings.collect(conn, c)
        assert c.요청 == [57001], f"보류를 또 물었다 (요청: {c.요청})"


class Test명령:
    def test_collect_가_표시_명령을_받는다(self, conn, db_path):
        상한까지(conn)
        conn.close()
        코드 = collect._main(["--db", str(db_path), "--보류", "회의본문", "55355", "--이유", "원천 결함"])
        assert 코드 == 0
        c = db.connect(db_path)
        assert c.execute("SELECT 실패종류 FROM 수집실패 WHERE 대상키='55355'").fetchone()[0] == "보류"

    def test_거부되면_비영으로_끝난다(self, conn, db_path):
        conn.close()
        코드 = collect._main(["--db", str(db_path), "--보류", "회의본문", "55355", "--이유", "원천 결함"])
        assert 코드 != 0
