"""`replace_의안심사` 와 `수집실패` 원장."""

import pytest

import db

의안 = {
    "의안번호": "2213298",
    "의안ID": "PRC_A",
    "의안명": "국민건강보험법 일부개정법률안",
    "제안자구분": "의원",
}

소관위단계 = ("소관위", "법사위", "본회의")


@pytest.fixture
def 의안conn(conn):
    db.upsert_위원회(conn, "보건복지위원회")
    db.upsert_위원회(conn, "보건복지위원회 법안심사제1소위원회", 상위="보건복지위원회")
    db.upsert_위원회(conn, "법제사법위원회")
    db.upsert_의안(conn, "TVBPMBILL11", 의안)
    return conn


def 소위행(위원회명=None, 회부일="2025-11-12"):
    return {
        "단계": "소위",
        "위원회명": 위원회명,
        "회부일": 회부일,
        "상정일": None,
        "처리일": None,
        "처리결과": None,
    }


class Test의안심사:
    def test_같은_입력을_두_번_써도_행이_늘지_않는다(self, 의안conn):
        """S3. `회차` 가 순서 기반이라 UPSERT 로는 덮어쓰기가 아니라 **삽입**이 된다.

        원천이 행을 재정렬하면 같은 사실이 다른 회차를 받고, 그러면 PK 가 안 부딪혀서
        에러 없이 행만 는다. 그래서 이 테이블은 DELETE 후 INSERT 다.
        """
        rows = [소위행("보건복지위원회 법안심사제1소위원회"), 소위행(None)]
        db.replace_의안심사(의안conn, "2213298", ("소위",), rows)
        db.replace_의안심사(의안conn, "2213298", ("소위",), rows)
        assert (
            의안conn.execute(
                "SELECT COUNT(*) FROM 의안심사 WHERE 의안번호='2213298'"
            ).fetchone()[0]
            == 2
        )

    def test_소위_패스가_소관위와_본회의_행을_지우지_않는다(self, 의안conn):
        """S14. 원천 셋이 **서로 다른 패스**에서 이 테이블을 쓴다.

        조건 없이 `DELETE WHERE 의안번호=?` 로 지우면 5단계(소위)가 2단계(소관위·법사위·
        본회의)의 행을 지운다. 소위 심사가 법률안의 79%에 걸리므로 그 79%가 본회의 행을
        잃는데, **A6 게이트는 본회의 행이 아예 사라진 경우를 검사하지 않아 초록불이다.**
        """
        db.replace_의안심사(
            의안conn,
            "2213298",
            소관위단계,
            [
                {"단계": "소관위", "위원회명": "보건복지위원회", "회부일": "2024-07-01"},
                {"단계": "법사위", "위원회명": "법제사법위원회", "회부일": "2025-12-01"},
                {"단계": "본회의", "처리일": "2026-01-07", "처리결과": "대안반영폐기"},
            ],
        )
        db.replace_의안심사(의안conn, "2213298", ("소위",), [소위행()])

        단계들 = [
            r[0]
            for r in 의안conn.execute(
                "SELECT 단계 FROM 의안심사 WHERE 의안번호='2213298' ORDER BY 단계"
            )
        ]
        assert 단계들 == ["법사위", "본회의", "소관위", "소위"]

    def test_같은_소위에_두_번_회부된_것이_회차로_구분된다(self, 의안conn):
        """실측: 같은 (의안, 소위) 조합의 중복이 103건이다.

        `(의안번호, 단계, 위원회명)` 을 키로 삼으면 그 103건이 PK 충돌로 사라진다.
        """
        db.replace_의안심사(
            의안conn,
            "2213298",
            ("소위",),
            [
                소위행("보건복지위원회 법안심사제1소위원회", "2025-11-12"),
                소위행("보건복지위원회 법안심사제1소위원회", "2026-02-03"),
            ],
        )
        회차 = [
            (r[0], r[1])
            for r in 의안conn.execute(
                "SELECT 회차, 회부일 FROM 의안심사 WHERE 의안번호='2213298' ORDER BY 회차"
            )
        ]
        assert 회차 == [(1, "2025-11-12"), (2, "2026-02-03")]

    def test_지우지_않을_단계를_넣으려_하면_거부한다(self, 의안conn):
        """넣기만 하고 안 지우면 **재실행마다 행이 는다.** 에러 없이."""
        with pytest.raises(ValueError, match="지우지 않는 단계"):
            db.replace_의안심사(
                의안conn, "2213298", ("소위",), [{"단계": "본회의", "처리일": "2026-01-07"}]
            )


class Test수집실패:
    def test_성공하면_원장에서_지운다(self, 의안conn):
        """S4. 옛 프로젝트가 성공 경로에서 `clear_failure()` 를 빠뜨려 원장이 영구히 빨갰다."""
        db.record_failure(의안conn, "의안요약", "2213298", "재시도", "network:ReadTimeout")
        assert db.failure_count(의안conn) == 1
        db.clear_failure(의안conn, "의안요약", "2213298")
        assert db.failure_count(의안conn) == 0

    def test_재시도할수록_시도횟수가_는다(self, 의안conn):
        for _ in range(3):
            db.record_failure(의안conn, "의안요약", "2213298", "재시도", "network:timeout")
        row = 의안conn.execute("SELECT 시도횟수, 상세 FROM 수집실패").fetchone()
        assert row[0] == 3
        assert row[1] == "network:timeout"

    def test_없는_것을_지워도_조용하다(self, 의안conn):
        """성공 경로가 "원장에 있었나"를 먼저 묻지 않아도 되게 한다."""
        db.clear_failure(의안conn, "의안요약", "2213298")

    def test_막힘은_상세가_덮여도_계속_빨갛다(self, 의안conn):
        """`상세` 에 게이트를 걸면 안 되는 이유 — 다음 재시도가 그 값을 갈아 끼운다.

        "값이 들어올 때까지 계속 빨갛다"를 약속하는 자리는 `실패종류='막힘'` 이다.
        """
        db.record_failure(의안conn, "의안표결", "2220257", "막힘", "check:표결결과=무효")
        db.record_failure(의안conn, "의안표결", "2220257", "막힘", "check:표결결과=이석")
        assert db.failure_count(의안conn, 실패종류="막힘") == 1

    def test_대상종류가_CHECK_밖이면_거부한다(self, 의안conn):
        """⚠️ 문서가 "수집실패에 남겨라"고 쓴 자리마다 대응하는 값이 있어야 한다.

        없으면 그 INSERT 가 CHECK 위반으로 죽고 **의안/회의 하나의 트랜잭션이 통째로
        롤백된다** — "부분 실패가 전체를 죽이지 않는다"가 깨진다.
        """
        with pytest.raises(ValueError, match="대상종류"):
            db.record_failure(의안conn, "의안본문", "2213298", "재시도", "x")
