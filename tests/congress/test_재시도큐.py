"""원장이 곧 재시도 큐라는 약속을, 대상 선정이 실제로 지키는가.

`db.record_failure` 의 머리말은 **"원장이 곧 재시도 큐다"** 라고 약속한다. 그런데 회의
본문의 대상 선정은 `이미 = SELECT 회의id FROM 회의` 하나로 정해져 원장을 아예 안 읽었다 —
약속과 코드가 다른 자리다.

⚠️ **이 어긋남은 빨간불도 안 내고 멈춘다.** 원장에 `'재시도'` 로 남은 회의가 무슨 이유로든
대상에서 빠지면 ① 다시 요청되지 않아 데이터가 안 채워지고 ② 시도횟수가 안 늘어
**A11(재시도 상한)이 영영 안 울리며** ③ `'막힘'` 이 아니라서 A4 도 안 가리킨다. 수집도
없고 경고도 없는 정지 상태 — 불변식 2("어디서 중단되든 다음 실행이 이어받는다")가 깨지는
가장 조용한 모양이다.
"""

import pytest

import db
import meetings


def 본문(회의id: int, class_id: int = 2) -> str:
    """`parse_minutes` 가 통과시키는 최소 회의록 — 의원 발언 한 블록."""
    return (
        f"<html><script>const mnts_id = {회의id}; const class_id = {class_id};</script>"
        "<div class='minutes_body'>"
        "<div id='spk_2' data-mem_id='7001' data-name='아무개' data-pos='위원장'>"
        "<div class='talk'><div class='txt'><span class='spk_sub'>말했다.</span></div></div>"
        "</div></div></html>"
    )


class 세는클라이언트:
    """`가짜클라이언트` 와 같은 셋을 흉내내되 **무엇을 물었는지 순서대로 남긴다.**

    대상 선정을 재는 테스트라 "몇 건 저장됐나"로는 부족하다 — 큐 항목을 **다시 물었는지**,
    그리고 표본이 잘릴 때 **먼저 물었는지**가 판정 대상이다.
    """

    def __init__(self, 열거):
        self.열거, self.요청 = list(열거), []

    def all_pages(self, api, **kw):
        if api == meetings.열거_API:
            return list(self.열거) if kw.get("CONF_DATE") == 2025 else []
        return []

    def text(self, url, **kw):
        회의id = int(kw["params"]["id"])
        self.요청.append(회의id)
        return 본문(회의id)


def 열거행(회의id: int, 위원회="국회운영위원회") -> dict:
    return {
        "CONFER_NUM": str(회의id), "CONF_ID": "N054999", "CLASS_NAME": "상임위원회",
        "COMM_NAME": 위원회, "CONF_DATE": "2025-09-06",
        "TITLE": f"제22대 제429회 제99차 {위원회} (2025년 09월 06일)",
        "SUB_NAME": "1. 간사 선임의 건",
    }


def 이미받은것으로(conn, 회의id: int) -> None:
    """그 회의가 `회의` 표에 있는 상태 — 옛 대상 선정은 이것만 보고 건너뛴다."""
    with db.트랜잭션(conn):
        위원회 = db.upsert_위원회(conn, "국회운영위원회")
        conn.execute(
            "INSERT INTO 회의 (회의id,회의코드,회의종류,위원회명,회기,차수,회의일자)"
            " VALUES (?,?,?,?,?,?,?)",
            (회의id, "N054999", "상임위원회", 위원회, 429, 99, "2025-09-06"),
        )


class Test큐가_대상에_합류한다:
    def test_이미_받았어도_원장이_재시도라면_다시_묻는다(self, conn):
        """⚠️ **"데이터가 있으니 다 됐다"가 이 약속을 깨는 자리다.** 한 번 성공한 뒤의
        재수집 실패는 데이터가 남아 있어 받은 것처럼 보이는데 원장에는 실패가 있다 —
        `record_failure` 의 머리말이 정확히 이 경우를 경고한다."""
        이미받은것으로(conn, 55355)
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
        c = 세는클라이언트([열거행(55355)])
        meetings.collect(conn, c)
        assert c.요청 == [55355], "큐에 있는데 다시 안 물었다"
        assert not conn.execute(
            "SELECT 1 FROM 수집실패 WHERE 대상종류='회의본문'").fetchone(), \
            "성공했는데 원장이 안 지워졌다 — 다음 실행도 영원히 다시 묻는다"

    def test_큐가_표본보다_먼저다(self, conn):
        """⚠️ **`--sample N` 은 목록 앞에서 자른다.** 큐를 뒤에 두면 표본 실행이 도는 동안
        실패한 회의는 영영 순번이 안 온다 — 매 실행 새 회의만 받고 큐는 그대로다."""
        이미받은것으로(conn, 55355)
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
        c = 세는클라이언트([열거행(57001), 열거행(55355)])
        meetings.collect(conn, c, 표본=1)
        assert c.요청 == [55355], f"표본 1건이 큐를 건너뛰었다 (요청: {c.요청})"

    def test_없음과_막힘은_큐가_아니다(self, conn):
        """`'없음'` 은 다시 물어도 같고 `'막힘'` 은 사람이 고칠 일이다. 큐에 넣으면
        전자는 매 실행 요청을 낭비하고 후자는 고쳐지지 않은 채 시도횟수만 오른다."""
        for i, 종류 in ((55355, "없음"), (55356, "막힘")):
            이미받은것으로(conn, i)
            with db.트랜잭션(conn):
                db.record_failure(conn, "회의본문", str(i), 종류, "무엇")
        c = 세는클라이언트([열거행(55355), 열거행(55356)])
        meetings.collect(conn, c)
        assert c.요청 == []

    def test_열거에서_사라져도_이미_받은_회의는_다시_묻는다(self, conn):
        """⚠️ **"할 수 있는데 안 한다"가 되면 안 된다.** 본문 URL 은 회의id 하나면 되고,
        회기·차수·위원회명은 이미 `회의` 행에 있다 — 열거가 그 회의를 안 줘도 다시 받을
        근거가 전부 우리 안에 있다. 로그만 남기면 원장은 영영 안 비고 시도횟수도 안 늘어
        **A11 조차 안 울린다.**"""
        이미받은것으로(conn, 55355)
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
        c = 세는클라이언트([열거행(57001)])       # 55355 가 열거에 없다
        meetings.collect(conn, c)
        assert 55355 in c.요청, f"열거에 없다고 포기했다 (요청: {c.요청})"
        assert not conn.execute(
            "SELECT 1 FROM 수집실패 WHERE 대상종류='회의본문'").fetchone()

    def test_상한에_닿은_것은_표본_앞을_막지_않는다(self, conn):
        """⚠️ **우선순위가 곧 굶김이다.** 큐를 앞에 두면 영영 안 풀리는 항목이 매 실행
        `--sample N` 의 앞자리를 통째로 차지해 **새 회의가 한 건도 안 들어온다** —
        큐를 합류시켜 고치려던 것과 정확히 같은 정지 상태를 반대쪽에서 만든다.
        상한에 닿은 것은 A11 이 빨갛게 가리키므로 사람이 보고 있다."""
        이미받은것으로(conn, 55355)
        with db.트랜잭션(conn):
            for _ in range(db.재시도상한):
                db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
        c = 세는클라이언트([열거행(55355), 열거행(57001)])
        meetings.collect(conn, c, 표본=1)
        assert c.요청 == [57001], f"상한에 닿은 것이 표본을 먹었다 (요청: {c.요청})"

    def test_한_번도_못_받았어도_상한에_닿으면_안_묻는다(self, conn):
        """⚠️ **상한을 큐에서만 지키면 우회로가 열린 채다.** 본문 실패는 저장 전에
        `continue` 하므로 `회의` 행을 남기지 않는다 — 그런 항목은 상한에 닿아 큐에서
        빠지는 순간 "아직 안 받은 회의"의 조건을 그대로 만족해 **둘째 목록으로 되돌아온다.**
        시도횟수가 6·7·8 로 끝없이 오르고, A11 이 그것을 "포기한 항목"이라 부르는 동안
        수집기는 매 실행 같은 요청을 계속 던진다. 영영 안 풀리는 항목이 요청 예산을
        영원히 먹는 자리다."""
        with db.트랜잭션(conn):
            for _ in range(db.재시도상한):
                db.record_failure(conn, "회의본문", "57001", "재시도", "network:ReadTimeout")
        c = 세는클라이언트([열거행(57001), 열거행(55355)])
        meetings.collect(conn, c)
        assert c.요청 == [55355], f"상한에 닿았는데 또 물었다 (요청: {c.요청})"

    def test_열거에서_사라진_큐는_로그에_남는다(self, conn):
        """⚠️ **원천이 그 회의를 더 이상 열거하지 않으면 우리가 할 수 있는 일이 없다** —
        회기·차수·위원회명이 열거에만 있어 `회의` 행을 만들 수 없다. 그런데 원장 행은
        그대로 남아 큐 건수가 줄지 않는다. **말하지 않으면 아무도 모르는 정지 상태다.**"""
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "99999", "재시도", "network:ReadTimeout")
        줄 = []
        meetings.collect(conn, 세는클라이언트([열거행(55355)]), 로그=줄.append)
        assert any("99999" in x for x in 줄), f"사라진 큐를 아무도 말하지 않았다: {줄}"


class Test다시_받는_것이_있던_것을_지우지_않는다:
    """⚠️ **큐 합류가 새로 여는 위험이다.** 여태 `회의` 표에 있는 회의는 두 번 다시 처리되지
    않았다 — 그래서 `DELETE 발언` · `DELETE 회의의안` 뒤의 재INSERT 가 언제나 0행 위에서
    돌았다. 큐가 합류하면 **이미 차 있는 회의 위에서 돌게 된다.**

    불변식 1(자동 수집이 DB 를 망가뜨려서는 안 된다)이 여기서 걸린다. 그리고 둘 다
    **감사에 안 잡힌다** — 회의의안 0건인 회의는 이미 468건이고(R12), 발언이 반토막 난
    회의는 A 계열 어느 등식도 안 건드린다.
    """

    def _큐에_넣는다(self, conn, 회의id: int) -> None:
        이미받은것으로(conn, 회의id)
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", str(회의id), "재시도", "network:ReadTimeout")

    def test_그날_열거에_안건이_없으면_있던_회의의안을_남긴다(self, conn):
        """열거가 그 회의의 안건을 안 주는 날 — 원천이 `SUB_NAME` 에서 의안번호를 뺐거나
        그날치가 덜 온 것이다. **둘 다 우리 행을 지울 근거가 못 된다.**"""
        self._큐에_넣는다(conn, 55355)
        with db.트랜잭션(conn):
            conn.execute("INSERT INTO 의안 (의안번호,의안ID,의안명) VALUES ('2200001','PRC_X','옛 안건')")
            conn.execute("INSERT INTO 회의의안 (회의id,의안번호) VALUES (55355,'2200001')")
        meetings.collect(conn, 세는클라이언트([열거행(55355)]))   # SUB_NAME 에 의안번호가 없다
        assert conn.execute(
            "SELECT COUNT(*) FROM 회의의안 WHERE 회의id=55355").fetchone()[0] == 1, \
            "빈 열거가 있던 회의의안을 지웠다"

    def test_그날_열거에_안건이_있으면_교체한다(self, conn):
        """지우지 않는 것이 목적이 아니다 — **바뀐 것은 반영해야 한다.**"""
        self._큐에_넣는다(conn, 55355)
        with db.트랜잭션(conn):
            for 번호 in ("2200001", "2200002"):
                conn.execute("INSERT INTO 의안 (의안번호,의안ID,의안명) VALUES (?,?,?)",
                             (번호, f"PRC_{번호}", "안건"))
            conn.execute("INSERT INTO 회의의안 (회의id,의안번호) VALUES (55355,'2200001')")
        행 = 열거행(55355)
        행["SUB_NAME"] = "1. 무슨 법률안(의안번호 2200002)"
        meetings.collect(conn, 세는클라이언트([행]))
        assert [r[0] for r in conn.execute(
            "SELECT 의안번호 FROM 회의의안 WHERE 회의id=55355")] == ["2200002"]

    def test_발언이_크게_줄면_바꾸지_않고_막힘으로_남긴다(self, conn):
        """⚠️ **본문이 반토막으로 와도 파싱은 성공한다.** `if not 발언들` 은 0건만 막는다 —
        1,356개짜리 회의가 3개로 오면 그대로 교체돼 발언 1,353행이 조용히 사라진다.
        `db.교체막는것` 이 원천 축소 앞에서 쓰기를 막는 이 저장소의 하나뿐인 손잡이다."""
        self._큐에_넣는다(conn, 55355)
        with db.트랜잭션(conn):
            conn.executemany(
                "INSERT INTO 발언 (회의id,순서,발언자명,내용) VALUES (55355,?,'아무개','말')",
                [(i,) for i in range(1, 21)])
        meetings.collect(conn, 세는클라이언트([열거행(55355)]))   # 본문은 발언 1개짜리다
        assert conn.execute(
            "SELECT COUNT(*) FROM 발언 WHERE 회의id=55355").fetchone()[0] == 20, \
            "20행이 1행으로 교체됐다"
        종류, 상세 = conn.execute(
            "SELECT 실패종류, 상세 FROM 수집실패 WHERE 대상종류='회의본문'").fetchone()
        assert 종류 == "막힘", "막았으면서 원장에 안 남기면 아무도 모른다"
        assert "20" in 상세 and "1" in 상세


class Test큐를_읽는_helper:
    def test_재시도만_돌려준다(self, conn):
        with db.트랜잭션(conn):
            db.record_failure(conn, "회의본문", "1", "재시도", "")
            db.record_failure(conn, "회의본문", "2", "없음", "")
            db.record_failure(conn, "회의본문", "3", "막힘", "")
            db.record_failure(conn, "의안표결", "4", "재시도", "")
        assert db.재시도큐(conn, "회의본문") == {"1"}
        assert db.재시도큐(conn, "의안표결") == {"4"}

    def test_상한에_닿으면_큐가_아니다(self, conn):
        """`A11` 이 그 상태를 **"포기한 항목"** 이라고 부른다 — 큐에 그대로 두면 그 이름이
        거짓이 되고, 영영 안 풀리는 것을 매 실행 다시 묻게 된다."""
        with db.트랜잭션(conn):
            for _ in range(db.재시도상한 - 1):
                db.record_failure(conn, "회의본문", "1", "재시도", "")
            for _ in range(db.재시도상한):
                db.record_failure(conn, "회의본문", "2", "재시도", "")
        assert db.재시도큐(conn, "회의본문") == {"1"}

    def test_포기한것은_상한에_닿은_재시도다(self, conn):
        """`재시도큐` 와 정확히 여집합이어야 한다 — 사이가 벌어지면 그 틈의 항목이 큐에도
        포기에도 없어 **둘째 목록으로 새어 나간다**(그 경로가 이 함수가 생긴 이유다)."""
        with db.트랜잭션(conn):
            for _ in range(db.재시도상한):
                db.record_failure(conn, "회의본문", "57001", "재시도", "network:ReadTimeout")
            db.record_failure(conn, "회의본문", "55355", "재시도", "network:ReadTimeout")
            db.record_failure(conn, "회의본문", "55356", "없음", "무엇")
            db.record_failure(conn, "회의본문", "55357", "막힘", "무엇")
        assert db.포기한것(conn, "회의본문") == {"57001"}
        assert db.재시도큐(conn, "회의본문") == {"55355"}

    def test_포기한것도_모르는_대상종류는_거부한다(self, conn):
        with pytest.raises(ValueError):
            db.포기한것(conn, "회의몬문")

    def test_모르는_대상종류는_거부한다(self, conn):
        """⚠️ 오타는 조용히 빈 집합이 되고, 빈 집합은 **큐가 비었다**와 구별되지 않는다."""
        with pytest.raises(ValueError):
            db.재시도큐(conn, "회의본무")
