"""원천이 줄어든 날 우리가 지우지 않는가.

⚠️ **원천이 이상한 날의 안전한 실패는 아무것도 안 지우는 것이다.** 국회 쓰기 경로 여럿이
"받은 것으로 통째로 갈아엎는다"로 되어 있어서, 원천이 빈손이거나 반토막인 날 그 빈손이
그대로 DB 가 됐다. 같은 주에 KOSIS 원천이 실제로 그렇게 줄었다.

여기 테스트가 흉내내는 것은 **행이 줄어든 정상 응답**이다 — 예외도 4xx 도 아니고 200 에
적은 행이라, 지금까지 아무 신호도 없이 통과했다.
"""

import pytest

from congress import bills
from congress import db as dbmod
from congress import members


# ── 의원위원회 ───────────────────────────────────────────────────────────────
#
# `members.collect` 은 `DELETE FROM 의원위원회` 로 시작한다. 원천이 빈손인 날 477행이
# 0행이 되고, **감사 게이트 중 어느 것도 이 표를 안 본다.**


class 의원원천:
    """`members.collect` 이 쓰는 네 API 를 흉내낸다. 위원 명단만 바꿔 끼운다."""

    def __init__(self, 위원명단, 현직=None):
        self.위원명단 = 위원명단
        self._현직 = 현직

    def all_pages(self, api, **kw):
        if api == members.위원회목록_API:
            return [{"CMIT_NM": "정무위원회"}, {"CMIT_NM": "법제사법위원회"}]
        if api == members.현직_API:
            현직 = self._현직 if self._현직 is not None else ["A1", "A2", "A3"]
            return [{"MONA_CD": c} for c in 현직]
        if api == members.의원전체_API:
            return [
                {"NAAS_CD": c, "NAAS_NM": f"의원{c}", "GTELT_ERACO": "제22대",
                 "PLPT_NM": "조국혁신당", "ELECD_NM": "비례대표"}
                for c in ("A1", "A2", "A3")
            ]
        if api == members.위원명단_API:
            return [{"MONA_CD": c, "DEPT_NM": w} for c, w in self.위원명단]
        raise AssertionError(f"모르는 API: {api}")


def _채운다(conn, 쌍들):
    """정상 수집 1회로 기준선을 만든다."""
    members.collect(conn, 의원원천(쌍들), 로그=lambda _: None)


def _의원위원회수(conn):
    return conn.execute("SELECT COUNT(*) FROM 의원위원회").fetchone()[0]


_정상 = [("A1", "정무위원회"), ("A2", "정무위원회"), ("A3", "법제사법위원회"),
        ("A1", "법제사법위원회"), ("A2", "법제사법위원회")]


class Test의원위원회_교체:
    def test_정상이면_교체한다(self, conn):
        _채운다(conn, _정상)
        assert _의원위원회수(conn) == 5

    def test_빈_응답이면_한_행도_안_지운다(self, conn):
        _채운다(conn, _정상)
        members.collect(conn, 의원원천([]), 로그=lambda _: None)
        assert _의원위원회수(conn) == 5, "원천이 빈손인 날 기존 배정이 통째로 사라졌다"

    def test_반토막_응답이면_안_지운다(self, conn):
        _채운다(conn, _정상)
        members.collect(conn, 의원원천(_정상[:2]), 로그=lambda _: None)
        assert _의원위원회수(conn) == 5

    def test_건너뛴_사실이_수집상태에_남는다(self, conn):
        """⚠️ **조용히 안 지우는 것은 조용히 지우는 것만큼 나쁘다.** 배정이 며칠째 낡았는데
        아무 데도 안 적히면, 조회자는 옛 배정을 오늘 것으로 읽는다."""
        _채운다(conn, _정상)
        members.collect(conn, 의원원천([]), 로그=lambda _: None)
        r = conn.execute(
            "SELECT 상태, 건수 FROM 수집상태 WHERE 대상='의원위원회' AND 키='전체'"
        ).fetchone()
        assert r is not None, "건너뛴 사실이 아무 데도 안 남았다"
        assert r[0] == "건너뜀" and r[1] == 0

    def test_정상으로_돌아오면_표지가_풀린다(self, conn):
        _채운다(conn, _정상)
        members.collect(conn, 의원원천([]), 로그=lambda _: None)
        members.collect(conn, 의원원천(_정상), 로그=lambda _: None)
        assert conn.execute(
            "SELECT 상태 FROM 수집상태 WHERE 대상='의원위원회' AND 키='전체'"
        ).fetchone()[0] == "완료"

    def test_기준선이_없으면_막지_않는다(self, conn):
        """첫 수집은 0 → N 이라 '감소'가 아니다. 여기서 막으면 빈 DB 를 영영 못 채운다."""
        _채운다(conn, _정상[:1])
        assert _의원위원회수(conn) == 1

    def test_조금_줄어드는_것은_통과시킨다(self, conn):
        """위원 재배정은 짝을 옮기지 지우지 않는다. 한두 짝의 변동까지 막으면
        **고칠 수 없는 빨간불**이 되고, 그러면 표 전체를 안 보게 된다.

        ⚠️ 짝이 중복이면 `parse_의원위원회` 가 접는다 — 위원회 이름을 서로 다르게 준다.
        """
        스무짝 = [("A1", f"제{i}위원회") for i in range(20)]
        _채운다(conn, 스무짝)
        assert _의원위원회수(conn) == 20
        members.collect(conn, 의원원천(스무짝[:19]), 로그=lambda _: None)
        assert _의원위원회수(conn) == 19


# ── 현직여부 ─────────────────────────────────────────────────────────────────
#
# 현직 판정의 근거는 "인적사항 API 에 있는가" 하나다. 그래서 그 API 가 줄어든 날
# **명단에서 빠진 의원이 전부 `현직여부=0`** 이 된다. 의원 행은 그대로 있고 컬럼 하나만
# 뒤집히므로 행수를 보는 게이트로는 구조적으로 안 잡힌다.


def _현직수(conn):
    return conn.execute("SELECT COUNT(*) FROM 의원 WHERE 현직여부=1").fetchone()[0]


class 열명원천(의원원천):
    """현직 감소를 재려면 모집단이 10명은 돼야 한다 — 3명에서 1명이 빠지면 33% 라
    임계값 10% 를 무조건 넘긴다."""

    _전원 = [f"A{i}" for i in range(10)]

    def all_pages(self, api, **kw):
        if api == members.의원전체_API:
            return [
                {"NAAS_CD": c, "NAAS_NM": f"의원{c}", "GTELT_ERACO": "제22대",
                 "PLPT_NM": "조국혁신당", "ELECD_NM": "비례대표"}
                for c in self._전원
            ]
        if api == members.현직_API:
            현직 = self._현직 if self._현직 is not None else self._전원
            return [{"MONA_CD": c} for c in 현직]
        return super().all_pages(api, **kw)


class Test현직여부:
    def test_정상이면_현직을_갱신한다(self, conn):
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)
        assert _현직수(conn) == 10

    def test_빈_현직명단이면_아무도_0으로_안_바꾼다(self, conn):
        """⚠️ **299명이 한꺼번에 전직이 되는 날, 의원 행수는 그대로다.**"""
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)
        members.collect(conn, 열명원천([], 현직=[]), 로그=lambda _: None)
        assert _현직수(conn) == 10

    def test_반토막_현직명단도_막는다(self, conn):
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)
        members.collect(conn, 열명원천([], 현직=열명원천._전원[:5]), 로그=lambda _: None)
        assert _현직수(conn) == 10

    def test_막혀도_이름_선거구는_갱신한다(self, conn):
        """⚠️ **현직 명단 하나가 이상하다고 의원 표 전체를 멈추면 안 된다.** 현직여부와
        정당(둘 다 현직 API 가 근거다)만 옛 값을 지키고 `ALLNAMEMBER` 컬럼은 평소대로
        들어와야 한다 — 안 그러면 원천이 이상한 하루가 그 표의 모든 컬럼을 하루 낡게 만든다."""
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)

        class 선거구바뀜(열명원천):
            def all_pages(self, api, **kw):
                행 = super().all_pages(api, **kw)
                if api == members.의원전체_API:
                    for r in 행:
                        r["ELECD_NM"] = "서울 강남구갑"
                return 행

        members.collect(conn, 선거구바뀜([], 현직=[]), 로그=lambda _: None)
        assert _현직수(conn) == 10
        assert conn.execute(
            "SELECT COUNT(*) FROM 의원 WHERE 선거구='서울 강남구갑'").fetchone()[0] == 10

    def test_건너뛴_사실이_수집상태에_남는다(self, conn):
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)
        members.collect(conn, 열명원천([], 현직=[]), 로그=lambda _: None)
        r = conn.execute(
            "SELECT 상태, 건수 FROM 수집상태 WHERE 대상='의원현직' AND 키='전체'").fetchone()
        assert r is not None and r[0] == "건너뜀" and r[1] == 0

    def test_한_명_줄어드는_것은_통과시킨다(self, conn):
        """보궐·사퇴는 실재한다. 그걸 막으면 현직 표가 영영 안 갱신된다."""
        members.collect(conn, 열명원천([], 현직=열명원천._전원), 로그=lambda _: None)
        members.collect(conn, 열명원천([], 현직=열명원천._전원[:9]), 로그=lambda _: None)
        assert _현직수(conn) == 9

    def test_첫_수집은_막지_않는다(self, conn):
        """기존 현직이 0인 빈 DB 에서 막으면 영영 못 채운다."""
        members.collect(conn, 열명원천([], 현직=열명원천._전원[:3]), 로그=lambda _: None)
        assert _현직수(conn) == 3


# ── 표결 명단 ────────────────────────────────────────────────────────────────
#
# 명단 응답이 비면 종전에는 `DELETE FROM 표결` 뒤에 아무것도 안 넣고 **원장까지 지웠다.**
# 오늘 데이터에서는 대상 선정(`번호 not in 이미`)이 기존 행을 가진 의안을 거르므로 DELETE 가
# 헛돌지만, `clear_failure` 는 그렇지 않다 — **빈손으로 받은 의안이 재시도 큐에서 빠진다.**
# 그리고 한 의안에 의결일이 둘이 되는 날(재의결) 두 번째 패스가 첫 패스 결과를 지운다.


def _집계(의결일):
    return {
        "BILL_NO": "2200001", "BILL_ID": f"PRC_투표{의결일}", "PROC_DT": 의결일,
        "MEMBER_TCNT": 3, "VOTE_TCNT": 3, "YES_TCNT": 3, "NO_TCNT": 0, "BLANK_TCNT": 0,
    }


class 표결원천:
    """`명단들` 을 순서대로 하나씩 내준다 — 같은 의안이 두 번 도는 경우를 재려면
    응답이 패스마다 달라야 한다."""

    def __init__(self, *명단들, 의결일=("2026-07-31",)):
        self.명단들 = list(명단들)
        self.의결일 = 의결일

    def all_pages(self, api, **kw):
        assert api == bills.표결집계_API
        return [_집계(d) for d in self.의결일]

    def json(self, api, **kw):
        assert api == bills.표결명단_API
        명단 = self.명단들.pop(0) if len(self.명단들) > 1 else self.명단들[0]
        return list(명단), len(명단)


@pytest.fixture
def 표결마당(conn):
    """의안 하나와 의원 셋. FK 부모가 먼저 서야 표결이 들어간다."""
    conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명) VALUES ('2200001','PRC_1','x')")
    for c in ("A1", "A2", "A3"):
        conn.execute("INSERT INTO 의원 (의원코드, 이름) VALUES (?,?)", (c, f"의원{c}"))
    return conn


_명단 = [{"BILL_NO": "2200001", "MONA_CD": c, "RESULT_VOTE_MOD": "찬성"}
        for c in ("A1", "A2", "A3")]


def _표결수(conn):
    return conn.execute("SELECT COUNT(*) FROM 표결").fetchone()[0]


class Test표결명단_빈응답:
    def test_정상이면_명단을_넣는다(self, 표결마당):
        bills.collect_표결(표결마당, 표결원천(_명단), 로그=lambda *a: None)
        assert _표결수(표결마당) == 3

    def test_빈_명단이면_원장에_재시도로_남는다(self, 표결마당):
        """⚠️ **빈 응답을 성공으로 확정하면 그 의안은 영영 안 받아진다.**
        `clear_failure` 가 돌아 재시도 큐에서 빠지는데, A10 만 빨갛고 왜인지는 안 남는다."""
        bills.collect_표결(표결마당, 표결원천([]), 로그=lambda *a: None)
        r = 표결마당.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 대상종류='의안표결' AND 대상키='2200001'"
        ).fetchone()
        assert r is not None, "빈 응답이 아무 흔적도 안 남겼다"
        assert r[0] == "재시도", "다음 실행이 다시 집어야 하는 것은 '막힘' 이 아니다"

    def test_같은_의안을_두_번_도는_날_두_번째_빈손이_첫_결과를_안_지운다(self, 표결마당):
        """⚠️ **재의결로 한 의안에 의결일이 둘이 되면 같은 실행에서 두 번 돈다.**

        대상 선정(`번호 not in 이미`)이 기존 행을 가진 의안을 거르지만, `이미` 는 루프
        **전에** 한 번 계산되므로 같은 실행 안에서 두 번 오른 의안은 안 걸린다. 두 번째
        패스가 빈손이면 `DELETE` 가 첫 패스의 명단을 지운다. 오늘 데이터에 그런 의안이
        0건이라 **지금은 안 터지지만, 터질 때 아무 신호가 없다.**
        """
        bills.collect_표결(
            표결마당,
            표결원천(_명단, [], 의결일=("2026-07-31", "2026-08-14")),
            로그=lambda *a: None,
        )
        assert _표결수(표결마당) == 3, "두 번째 빈 응답이 첫 패스의 명단을 지웠다"

    def test_빈_명단이어도_집계는_들어간다(self, 표결마당):
        """집계와 명단은 다른 요청이다. 명단이 비었다고 집계까지 버리면 A8·A10 이
        서로를 못 가리킨다."""
        bills.collect_표결(표결마당, 표결원천([]), 로그=lambda *a: None)
        assert 표결마당.execute("SELECT COUNT(*) FROM 표결집계").fetchone()[0] == 1

    def test_다시_받으면_원장이_풀린다(self, 표결마당):
        bills.collect_표결(표결마당, 표결원천([]), 로그=lambda *a: None)
        bills.collect_표결(표결마당, 표결원천(_명단), 로그=lambda *a: None)
        assert 표결마당.execute(
            "SELECT COUNT(*) FROM 수집실패 WHERE 대상종류='의안표결'").fetchone()[0] == 0
        assert _표결수(표결마당) == 3


# ── 발의자 ───────────────────────────────────────────────────────────────────
#
# 두 경로가 다르게 위험하다. 전량 경로는 **파싱은 성공했는데 쓸 코드가 하나도 안 남았을 때**
# 기존 발의자를 지우고, 비법률안 보정 경로는 빈 응답에 아무 흔적도 안 남겨 A3 가 영영
# 빨간데 왜인지를 못 준다.


class 발의자원천:
    def __init__(self, 전량행=(), 보정행=()):
        self.전량행, self.보정행 = list(전량행), list(보정행)

    def all_pages(self, api, **kw):
        assert api == bills.발의자_API
        return self.전량행

    def json(self, api, **kw):
        assert api == bills.제안자_API
        return list(self.보정행), len(self.보정행)


def _발의자수(conn, 번호="2200001"):
    return conn.execute(
        "SELECT COUNT(*) FROM 발의자 WHERE 의안번호=?", (번호,)).fetchone()[0]


@pytest.fixture
def 발의자마당(conn):
    conn.execute("INSERT INTO 의원 (의원코드, 이름) VALUES ('A1','의원A1')")
    return conn


class Test발의자_전량경로:
    def test_아는_코드가_하나도_없으면_기존_발의자를_안_지운다(self, 발의자마당):
        """⚠️ **파싱 성공과 저장 가능은 다르다.** `등 N인` 검사는 통과했는데 코드가 전부
        우리 의원 표 밖이면 `쓸것` 이 비고, 그 상태로 `DELETE` 가 돈다 — 어제까지 있던
        발의자가 사라지는데 파싱은 성공했으므로 원장에도 안 남는다."""
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명) VALUES ('2200001','PRC_1','x')")
        conn.execute("INSERT INTO 발의자 (의안번호, 의원코드, 역할)"
                     " VALUES ('2200001','A1','대표발의')")
        bills.collect_발의자(conn, 발의자원천(전량행=[{
            "BILL_NO": "2200001", "PROPOSER": "모르는의원", "RST_MONA_CD": "ZZZ9999Z",
            "PUBL_MONA_CD": "",
        }]), 로그=lambda *a: None)
        assert _발의자수(conn) == 1, "아는 코드가 0개인 응답이 기존 발의자를 지웠다"

    def test_그_사실이_원장에_재시도로_남는다(self, 발의자마당):
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명) VALUES ('2200001','PRC_1','x')")
        bills.collect_발의자(conn, 발의자원천(전량행=[{
            "BILL_NO": "2200001", "PROPOSER": "모르는의원", "RST_MONA_CD": "ZZZ9999Z",
            "PUBL_MONA_CD": "",
        }]), 로그=lambda *a: None)
        r = conn.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 대상종류='발의자' AND 대상키='2200001'"
        ).fetchone()
        assert r is not None and r[0] == "재시도"

    def test_아는_코드가_하나라도_있으면_평소대로_교체한다(self, 발의자마당):
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명) VALUES ('2200001','PRC_1','x')")
        bills.collect_발의자(conn, 발의자원천(전량행=[{
            "BILL_NO": "2200001", "PROPOSER": "A1의원 등 2인",
            "RST_MONA_CD": "A1", "PUBL_MONA_CD": "ZZZ9999Z",
        }]), 로그=lambda *a: None)
        assert _발의자수(conn) == 1


class Test발의자_비법률안_보정:
    def test_빈_응답이_원장에_남는다(self, 발의자마당):
        """⚠️ **A3 는 '발의자가 0인 의안'을 세지만 왜 0인지는 모른다.** 빈 응답이 아무
        흔적을 안 남기면 그 빨간불이 영영 설명되지 않는다."""
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                     " VALUES ('2200002','PRC_2','결의안','의원')")
        bills.collect_발의자(conn, 발의자원천(보정행=[]), 로그=lambda *a: None)
        r = conn.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 대상종류='발의자' AND 대상키='2200002'"
        ).fetchone()
        assert r is not None and r[0] == "재시도"

    def test_받으면_원장이_풀린다(self, 발의자마당):
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                     " VALUES ('2200002','PRC_2','결의안','의원')")
        bills.collect_발의자(conn, 발의자원천(보정행=[]), 로그=lambda *a: None)
        bills.collect_발의자(conn, 발의자원천(보정행=[
            {"NASS_CD": "A1", "REP_DIV": "대표발의"}]), 로그=lambda *a: None)
        assert _발의자수(conn, "2200002") == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM 수집실패 WHERE 대상종류='발의자'").fetchone()[0] == 0

    def test_보정이_성공해도_막힘은_안_지운다(self, 발의자마당):
        """⚠️ **'막힘' 은 사람이 고칠 때까지 감사가 계속 가리킨다는 계약이다.**

        `등 N인` 이 코드 수와 어긋난 법률안은 첫 루프가 '막힘' 으로 남기는데, 발의자가
        0행이라 그 의안이 비법률안 보정 대상(`남은`)에도 오른다. 거기서 성공했다고
        원장을 통째로 지우면 **파서가 못 읽는 형식이 왔다는 신호가 사라진다** — 데이터
        구멍은 메워졌어도 원천이 우리가 못 읽는 것을 준다는 사실은 그대로다.
        """
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명, 제안자구분)"
                     " VALUES ('2200001','PRC_1','x','의원')")
        bills.collect_발의자(conn, 발의자원천(
            전량행=[{"BILL_NO": "2200001", "PROPOSER": "누구의원 등 10인",
                    "RST_MONA_CD": "A1", "PUBL_MONA_CD": ""}],
            보정행=[{"NASS_CD": "A1", "REP_DIV": "대표발의"}],
        ), 로그=lambda *a: None)
        assert _발의자수(conn) == 1, "보정 경로가 발의자를 채웠어야 한다"
        assert conn.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 대상종류='발의자' AND 대상키='2200001'"
        ).fetchone()[0] == "막힘", "보정 성공이 다른 경로의 '막힘' 을 지웠다"


# ── 감사 게이트 ──────────────────────────────────────────────────────────────


def _게이트(conn, 코드):
    """`audit.py` 의 SQL 을 그대로 태운다 — 여기 복사본을 두면 게이트가 바뀔 때 갈린다."""
    from congress import audit
    sql = next(s for 번호, _, s in audit.게이트 if 번호 == 코드)
    return conn.execute(sql).fetchone()[0] or 0


class Test건너뜀_게이트:
    def test_정상이면_0(self, conn):
        _채운다(conn, _정상)
        assert _게이트(conn, "A12") == 0

    def test_건너뛰면_빨갛다(self, conn):
        """⚠️ **A12 없이는 이 사고가 어느 게이트에도 안 걸린다.** 표는 옛 값을 그대로
        들고 있어 행수 등식이 전부 맞고, 그래서 33개 게이트가 전부 초록이다."""
        _채운다(conn, _정상)
        members.collect(conn, 의원원천([]), 로그=lambda _: None)
        assert _게이트(conn, "A12") == 1

    def test_두_자리가_함께_건너뛰면_둘_다_센다(self, conn):
        members.collect(conn, 열명원천(_정상, 현직=열명원천._전원), 로그=lambda _: None)
        members.collect(conn, 열명원천([], 현직=[]), 로그=lambda _: None)
        assert _게이트(conn, "A12") == 2

    def test_원천이_돌아오면_저절로_풀린다(self, conn):
        """사람이 손대야 풀리는 빨간불이면 안 된다 — 원인은 우리 쪽이 아니다."""
        _채운다(conn, _정상)
        members.collect(conn, 의원원천([]), 로그=lambda _: None)
        members.collect(conn, 의원원천(_정상), 로그=lambda _: None)
        assert _게이트(conn, "A12") == 0

    def test_빈_DB_에서도_0이다(self, conn):
        """아직 한 번도 안 돈 DB 는 '건너뛴' 것이 아니다. 여기서 빨개지면 첫 수집을
        시작조차 못 한다."""
        assert _게이트(conn, "A12") == 0


# ── 빈 목록이 코드를 죽이는 자리 ─────────────────────────────────────────────


class Test빈_의안목록이_수집을_죽이지_않는다:
    """⚠️ **`차집합` 이 빈 목록에서 `IndexError` 로 죽었다.** 원천이 의안 목록을 빈손으로
    주는 날 수집이 트레이스백과 함께 종료코드 1로 끝나는데, 워크플로는 1을 「게이트 위반 —
    사람이 봐야 한다」로 읽는다. 원천이 빈손인 것은 데이터 사고가 아니라 원천 사고다.

    그리고 죽으면 **뒤의 감사가 아예 안 돌아** 무엇이 이상한지도 안 남는다 — 빈손을
    잡으라고 있는 게이트가 빈손 때문에 실행되지 않는다.
    """

    def test_번호가_하나도_없으면_죽지_않는다(self, conn):
        class 빈원천:
            def json(self, api, **kw):
                raise AssertionError("탐침할 기준이 없는데 원천을 불렀다")

        assert bills.차집합(set(), 빈원천(), 로그=lambda *a: None) == []

    def test_번호가_있으면_평소대로_탐침한다(self, conn):
        class 한번만:
            def __init__(self): self.호출 = 0
            def json(self, api, **kw):
                self.호출 += 1
                return [], 0          # INFO-200 이 이어져 탐침이 끝난다

        c = 한번만()
        bills.차집합({"2200005"}, c, 로그=lambda *a: None)
        assert c.호출 > 0, "번호가 있는데 탐침을 안 했다"

# ── 불완전한 목록이 밖으로 나가는 모양 ────────────────────────────────────────


class Test불완전한목록의_종료코드:
    """⚠️ **`all_pages` 가 예외를 올리게 된 이상, 그 예외가 나가는 모양도 계약이다.**

    안 잡으면 트레이스백과 함께 종료코드 1로 나가는데, 워크플로는 1을 **「게이트 위반 —
    사람이 봐야 한다」** 로 읽는다(`collect.yml`). 원천이 한 번 딸꾹질한 날 사람이
    데이터 사고를 조사하러 들어가게 된다. 2는 「중단 — 다음 실행이 이어받는다」다.
    """

    def test_원천이_모자라게_주면_2로_나간다(self, db_path, monkeypatch):
        from congress import collect
        from congress import net

        class 모자란원천:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *e): pass
            def close(self): pass
            def all_pages(self, api, **kw):
                raise net.APIError(f"{api}: 총건수 100 인데 40 행만 왔다 — 불완전한 열거다")

        dbmod.connect(db_path).close()
        monkeypatch.setattr(net, "Client", 모자란원천)
        monkeypatch.setattr("sys.argv", ["collect.py", "--db", str(db_path)])
        assert collect._main() == 2

    def test_트레이스백을_뱉지_않는다(self, db_path, monkeypatch, capsys):
        from congress import collect
        from congress import net

        class 모자란원천:
            def __init__(self, *a, **k): pass
            def __enter__(self): return self
            def __exit__(self, *e): pass
            def close(self): pass
            def all_pages(self, api, **kw):
                raise net.APIError("불완전한 열거다")

        dbmod.connect(db_path).close()
        monkeypatch.setattr(net, "Client", 모자란원천)
        monkeypatch.setattr("sys.argv", ["collect.py", "--db", str(db_path)])
        collect._main()
        찍힘 = capsys.readouterr()
        assert "Traceback" not in 찍힘.err
        assert "불완전한 열거" in 찍힘.err, "무엇이 왜 멈췄는지가 로그에 있어야 한다"


# ── 코덱스 교차 검토가 잡은 것 ───────────────────────────────────────────────


class Test게이트가_옛_DB_에서_안_죽는다:
    """⚠️ **새 게이트가 새 표를 보면, 그 표가 없는 DB 에서 감사가 통째로 죽는다.**

    `audit.py` 단독 실행·`collect.py --audit-only`·백업 사본 감사는 셋 다 `init_schema`
    를 안 부른다(일부러 그렇다 — 감사가 DB 를 고치면 감사가 아니다). 그래서 이행 전
    DB 나 **어제 뜬 백업 세대**에 감사를 걸면 `no such table` 로 죽는다. 백업은 오래
    남으므로 이건 지나가는 창이 아니라 영구적인 구멍이다.
    """

    def test_수집상태가_없어도_감사가_돈다(self, db_path):
        """**하나가 깨진 것과 전부를 모르는 것은 다르다.** 감사가 통째로 죽으면 나머지
        열두 게이트의 답도 못 듣는다 — 그게 이 검사가 지키는 것이다."""
        import sqlite3
        from congress import audit
        빈 = sqlite3.connect(db_path)
        dbmod.init_schema(빈)
        빈.execute("DROP TABLE 수집상태")
        게, 보, _ = audit.run(빈)          # 죽지 않는다
        값 = {번호: (v, 이름) for 번호, 이름, v, _ in 게}
        assert len(게) == len(audit.게이트), "게이트 하나가 깨지고 나머지가 사라졌다"
        assert 값["A12"][0] != 0, "표가 없는 것은 통과가 아니다"
        assert "질의가 깨졌다" in 값["A12"][1], "왜 빨간지가 이름에 있어야 한다"
        assert 값["A0"][0] != 0, "표가 없다는 사실은 A0 도 진다"
        assert len(보) == len(audit.보고), "보고값도 통째로 사라지면 안 된다"
        빈.close()


class Test빈_코드행이_발의자를_안_지운다:
    """⚠️ `parse_proposers` 는 코드 필드가 **둘 다 비어 있어도** `([], True)` 를 준다 —
    `등 N인` 이 없으니 정합성 검사를 건너뛰고 성공으로 본다. 그래서 `쌍` 이 비고,
    `쌍 and not 쓸것` 은 거짓이 되어 그대로 `DELETE` 가 돈다."""

    def test_코드가_아예_없는_행이_기존_발의자를_안_지운다(self, 발의자마당):
        conn = 발의자마당
        conn.execute("INSERT INTO 의안 (의안번호, 의안ID, 의안명) VALUES ('2200001','PRC_1','x')")
        conn.execute("INSERT INTO 발의자 (의안번호, 의원코드, 역할)"
                     " VALUES ('2200001','A1','대표발의')")
        bills.collect_발의자(conn, 발의자원천(전량행=[{
            "BILL_NO": "2200001", "PROPOSER": "", "RST_MONA_CD": "", "PUBL_MONA_CD": "",
        }]), 로그=lambda *a: None)
        assert _발의자수(conn) == 1, "코드 0개 응답이 기존 발의자를 지웠다"


class Test재의결_빈손이_못_푸는_빨간불을_안_만든다:
    """⚠️ **원장에 남기는 순간 그것이 풀릴 길이 있는지 확인해야 한다.**

    한 의안에 의결일이 둘일 때 첫 명단이 들어오고 둘째가 비면, 그 의안은 이제
    `표결` 행을 가지므로 다음 실행의 `이미` 에 걸려 **다시 요청되지 않는다.** 그런데
    원장에는 '재시도' 가 남아 시도횟수가 5에 닿으면 A11 이 영영 빨갛고 **사람이 할 수
    있는 일이 없다** — 이 파일이 처음부터 경고한 바로 그 모양이다.
    """

    def test_첫_패스가_성공했으면_원장에_안_남긴다(self, 표결마당):
        bills.collect_표결(
            표결마당,
            표결원천(_명단, [], 의결일=("2026-07-31", "2026-08-14")),
            로그=lambda *a: None,
        )
        assert _표결수(표결마당) == 3
        assert 표결마당.execute(
            "SELECT COUNT(*) FROM 수집실패 WHERE 대상종류='의안표결'").fetchone()[0] == 0, (
            "다음 실행이 다시 집지 않을 의안을 재시도 큐에 넣었다")

    def test_한_번도_못_받았으면_원장에_남긴다(self, 표결마당):
        """반대쪽 — 명단을 통째로 못 받은 의안은 다음 실행이 실제로 다시 집으므로
        '재시도' 가 맞다."""
        bills.collect_표결(표결마당, 표결원천([]), 로그=lambda *a: None)
        assert 표결마당.execute(
            "SELECT 실패종류 FROM 수집실패 WHERE 대상종류='의안표결'").fetchone()[0] == "재시도"


class TestR14_는_지금을_기준으로_잰다:
    """⚠️ **`갱신일시 - 상태시작일시` 는 수집이 멈추면 같이 멈춘다.** verify 가 빨갛거나
    락에 막혀 수집이 아예 안 도는 동안 R14 는 계속 `0일` 이라고 답하는데, 그동안
    그 표는 실제로 며칠씩 낡는다 — **낡음을 재는 값이 낡음에 가려진다.**"""

    def test_옛_기록을_지금_기준으로_잰다(self, conn):
        from congress import audit
        conn.execute(
            "INSERT INTO 수집상태 (대상, 키, 상태, 건수, 갱신일시, 상태시작일시)"
            " VALUES ('의원위원회','전체','건너뜀',0,"
            "         datetime('now','localtime','-9 days'),"
            "         datetime('now','localtime','-9 days'))")
        sql = next(s for 번호, _, s in audit.보고 if 번호 == "R14")
        assert "9일" in conn.execute(sql).fetchone()[0]
