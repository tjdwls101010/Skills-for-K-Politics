#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27", "selectolax>=0.3"]
# ///
"""수집 오케스트레이션 — **유일한 진입점.**

`--audit-only` 로 아무것도 받지 않고 "이번에 무엇을 할 것인가"만 볼 수 있다. 규모가 궁금할 때
추측하거나 시험 삼아 돌려 보지 않아도 되게 하려는 것이다.

**어디서 죽어도 다음 실행이 이어받는다.** 진행 상태를 별도 파일에 두지 않고 DB 자체가 안다 —
체크포인트 파일을 두면 DB 와 어긋나는 세 번째 상태가 생기고, 어긋났을 때 어느 쪽이 맞는지
아무도 모른다.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from congress import audit  # noqa: E402
from congress import bills  # noqa: E402
from congress import db  # noqa: E402
from congress import meetings  # noqa: E402
from congress import members  # noqa: E402
from congress import net  # noqa: E402

# ── 할 일 미리보기 ────────────────────────────────────────────────────────────
계획질의 = [
    ("의안 상세 미수집", "SELECT COUNT(*) FROM 의안 WHERE 수집시각 IS NULL"),
    (
        "제안이유 미수집",
        "SELECT COUNT(*) FROM 의안 b WHERE 제안이유및주요내용 IS NULL"
        " AND NOT EXISTS (SELECT 1 FROM 수집실패 f WHERE f.대상종류='의안요약'"
        "                 AND f.대상키=b.의안번호 AND f.실패종류='없음')",
    ),
    (
        "공포 미확인 가결 의안",
        "SELECT COUNT(*) FROM 의안 WHERE 처리결과 IN ('원안가결','수정가결') AND 공포일 IS NULL",
    ),
    (
        "표결 명단 미수집",
        "SELECT COUNT(*) FROM 표결집계 a"
        " WHERE NOT EXISTS (SELECT 1 FROM 표결 v WHERE v.의안번호=a.의안번호)",
    ),
    (
        "대안 미조회",
        "SELECT COUNT(*) FROM 의안 b WHERE b.제안자구분='위원장' AND b.의안명 LIKE '%(대안)%'"
        " AND NOT EXISTS (SELECT 1 FROM 의안 o WHERE o.대안의안번호 = b.의안번호)",
    ),
    ("재시도 큐", "SELECT COUNT(*) FROM 수집실패 WHERE 실패종류='재시도'"),
]

행수질의 = [
    (t, f"SELECT COUNT(*) FROM {t}")
    for t in ("위원회", "의원", "의원위원회", "의안", "의안심사", "발의자",
              "표결집계", "표결", "회의", "발언", "회의의안")
]


def 계획내기(conn) -> None:
    print("\n이번에 무엇을 할 것인가")
    print("─" * 52)
    for 이름, sql in 계획질의:
        print(f"  {이름:<28} {conn.execute(sql).fetchone()[0]:>10,}")
    print("\n  (회의는 열거해 봐야 안다 — 네트워크가 필요하므로 여기 없다)")


def 행수(conn) -> dict[str, int]:
    return {t: conn.execute(sql).fetchone()[0] for t, sql in 행수질의}


def _곁기록에_담는다(곁기록: dict, 게, 보) -> None:
    """감사 결과를 요약이 읽을 수 있는 모양으로 옮긴다.

    ⚠️ **`해석` 을 빼지 마라.** 게이트 이름은 "무엇을 셌나"이지 "그래서 무슨 일이 났나"가
    아니다. 그 문장은 지금 stdout 에만 있어, 요약 표만 보는 사람에게는 닿지 않는다.
    """
    곁기록["감사실행"] = True
    곁기록["게이트"] = [{"번호": 번호, "이름": 이름, "값": v, "ok": ok,
                     "해석": audit._해석.get(번호) if not ok else None}
                    for 번호, 이름, v, ok in 게]
    곁기록["보고"] = [{"번호": 번호, "이름": 이름, "값": v} for 번호, 이름, v in 보]


def _결과쓰기(경로, 곁기록) -> None:
    """곁기록을 원자적으로 남긴다.

    ⚠️ **여기서 죽어 수집 판정을 뒤집지 않는다.** 이 파일은 요약을 예쁘게 그리려고
    남기는 것이지 수집의 일부가 아니다 — 못 쓴 날은 요약이 로그 꼬리로 물러나면 된다.
    """
    if not 경로:
        return
    try:
        임시 = Path(f"{경로}.tmp")
        임시.write_text(json.dumps(곁기록, ensure_ascii=False, indent=2, default=str))
        임시.replace(경로)
    except Exception as e:                                    # noqa: BLE001
        print(f"⚠️ 결과 곁기록을 못 남겼다: {e}", file=sys.stderr)


def _main(argv: list[str] | None = None) -> int:
    """인자를 읽고 **곁기록을 반드시 남기는 자리.** 일은 `_돈다` 가 한다.

    ⚠️ **`try` 를 `쓸_DB()` 보다 안쪽에 두지 마라.** 유령 DB 거부가 바깥에 남아,
    경로를 오타 낸 날 곁기록 없이 죽고 요약이 아무 말도 못 한다.

    ⚠️ **약속은 "모든 종료"가 아니라 "인자를 읽은 뒤 파이썬이 통제하는 종료"다.**
    SIGKILL·러너 유실·잡 타임아웃에서는 `finally` 가 안 돈다 — 그 경우는 요약 쪽의
    "곁기록이 없다" 갈래가 받는다.
    """
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    ap.add_argument("--result", metavar="경로",
                    help="판정·게이트·보고값을 JSON 한 장으로 남긴다 (요약이 읽는다)")
    ap.add_argument("--rate", type=float, default=8.0)
    ap.add_argument("--meeting-rate", type=float, default=6.0,
                    help="회의 본문은 회당 0.7MB 라 따로 낮춘다")
    ap.add_argument("--sample", type=int,
                    help="비싼 두 패스(제안이유·회의본문)를 이 건수만 받는다")
    ap.add_argument("--audit-only", action="store_true",
                    help="아무것도 받지 않고 할 일과 감사만 낸다")
    ap.add_argument("--보류", nargs=2, metavar=("대상종류", "대상키"),
                    help="상한에 닿은 포기를 사람이 확인했다고 표시한다 (A11 에서 빠진다)")
    ap.add_argument("--이유", help="--보류 의 이유. 원장의 상세에 남는다")
    a = ap.parse_args(argv)
    # ⚠️ **이름을 `결과` 로 두지 마라.** 아래 `if 결과 := db.migrate(conn)` 이 같은
    #    이름을 다시 묶어, 표를 담는 순간 `KeyError` 로 죽는다 (실측).
    곁기록: dict = {"코퍼스": "congress", "시각": None, "종류": "예외", "판정": 1,
                 "감사실행": False, "표": [], "유량": {}, "게이트": [], "보고": []}
    잰다 = time.monotonic()
    try:
        # ⚠️ **시계를 `try` 밖에서 읽지 마라.** 여기서 죽으면 곁기록이 통째로 안 남고,
        #    그런 날일수록 요약이 필요하다. 못 읽으면 `시각` 만 비운다.
        곁기록["시각"] = db._지금()
        곁기록["판정"] = 코드 = _돈다(a, 곁기록)
        return 코드
    finally:
        곁기록["소요초"] = round(time.monotonic() - 잰다, 1)
        _결과쓰기(a.result, 곁기록)


def _돈다(a, 곁기록: dict) -> int:
    # ⚠️ **연결은 락 뒤에 연다** — `connect()` 가 파일을 만들고 WAL 설정을 파일에
    #    남기므로, 앞에 두면 물러날 실행도 DB 를 건드린 뒤에 물러난다. `쓸_DB` 가
    #    락보다 먼저 유령 방지를 통과시킨다.
    경로 = db.쓸_DB(a.db)

    if a.보류:
        # ⚠️ **락 안에서 쓴다.** 예약 수집이 같은 행을 갱신하는 중일 수 있다.
        with db.락(경로) as L:
            if not L.잡음:
                곁기록["종류"] = "락"
                return 3
            conn = db.connect(경로)
            try:
                db.보류(conn, a.보류[0], a.보류[1], a.이유 or "")
            except ValueError as e:
                print(f"🔴 {e}", file=sys.stderr)
                곁기록["종류"] = "거부"
                return 1
            finally:
                conn.close()
        print(f"보류 표시: {a.보류[0]} {a.보류[1]} — 다음 감사부터 A11 이 안 센다. R6 에는 남는다.")
        곁기록["종류"] = "정상"
        return 0

    # ⚠️ **감사만 볼 때는 스키마를 건드리지 않는다.** 종전에는 여기 오기 전에
    #    `init_schema`·`migrate` 가 돌아서, "무엇이 밀렸나"만 보려는 호출이
    #    **락 밖에서 DB 를 고쳤다.**
    if a.audit_only:
        conn = db.connect(경로)
        계획내기(conn)
        게, 보, 실패 = audit.run(conn)
        print()
        for 번호, 이름, v, ok in 게:
            print(f"  {번호:<4} {이름:<42} {v:>9,}  {'🟢' if ok else '🔴'}")
        conn.close()
        _곁기록에_담는다(곁기록, 게, 보)
        곁기록["종류"] = "게이트" if 실패 else "정상"
        # ⚠️ **빨간 게이트에 0을 내면 게이트가 없는 것과 같다.** 종전에는 무조건 0이라,
        #    "무엇이 밀렸나"만 보려는 호출이 빨간 DB 를 초록으로 읽었다. 판정을 아무도
        #    안 듣는다면 게이트를 아무리 촘촘히 달아도 소용이 없다.
        return 1 if 실패 else 0

    시작 = time.monotonic()
    끝냄 = False
    try:
        with db.락(경로) as L:
            if not L.잡음:
                곁기록["종류"] = "락"
                # ⚠️ **0 을 돌려주지 마라.** 물러난 실행과 끝까지 간 실행이 같은 색이면
                #    겹침이 매일 나도 워크플로는 매일 초록이다 — 무엇도 안 받은 날을
                #    아무도 못 본다. 워크플로가 3 을 「건너뜀」으로 읽는다(실패 아님).
                return 3
            conn = db.connect(경로)
            # ⚠️ **스키마 변경은 반드시 락 안에서 한다.** 밖에 두면 두 실행이 동시에
            #    테이블 재구축에 들어갈 수 있고, 그건 되돌릴 방법이 없다.
            db.init_schema(conn)
            # 매 실행 첫머리에 주석 동기화. 구조가 다르면 손대지 않고 이름만 돌려준다.
            if 결과 := db.migrate(conn):
                for 이름 in 결과["주석교체"]:
                    print(f"스키마 주석 교체: {이름}")
                for 이름 in 결과["구조불일치"]:
                    print(f"⚠️ 구조 불일치 {이름} — 손대지 않았다. ALTER TABLE 은 사람이 한다")
            이전 = 행수(conn)
            with net.Client(rate=a.rate, 로그=print) as c:
                print("── 위원회 · 의원 ──")
                members.collect(conn, c)
                print("── 의안 ──")
                번호들 = bills.collect_목록(conn, c)["번호들"]
                bills.collect_보완(conn, c, 번호들)
                bills.collect_소위(conn, c)
                bills.collect_발의자(conn, c)
                bills.collect_표결(conn, c)
                bills.collect_대안(conn, c)
                bills.collect_요약(conn, c, 표본=a.sample)
            print("── 회의 ──")
            with net.Client(rate=a.meeting_rate, 로그=print) as c:
                meetings.collect(conn, c, 표본=a.sample)
            끝냄 = True
    except KeyboardInterrupt:
        print("\n⚠️ 중단됐다. **다음 실행이 이어받는다** — 사람이 할 일은 없다.", file=sys.stderr)
        곁기록["종류"] = "중단"
        return 2
    except net.APIError as e:
        # ⚠️ **원천의 딸꾹질을 게이트 위반과 같은 색으로 내보내지 마라.** 안 잡으면
        #    트레이스백과 함께 1로 나가는데, 워크플로는 1을 「사람이 봐야 한다」로 읽는다
        #    (`collect.yml`). 원천이 목록을 모자라게 준 하루가 데이터 사고 조사로 번진다.
        #    2는 「중단 — 다음 실행이 이어받는다」이고, 이 경우가 정확히 그것이다.
        print(f"\n⚠️ 원천이 온전한 답을 안 줬다: {e}", file=sys.stderr)
        print("**다음 실행이 이어받는다** — 사람이 할 일은 없다.", file=sys.stderr)
        곁기록["종류"] = "중단"
        return 2

    이후 = 행수(conn)
    print(f"\n{'테이블':<12} {'이전':>12} {'이후':>12} {'증분':>10}")
    print("─" * 50)
    for t in 이후:
        증분 = 이후[t] - 이전[t]
        곁기록["표"].append({"이름": t, "이전": 이전[t], "이후": 이후[t], "증분": 증분})
        print(f"{t:<12} {이전[t]:>12,} {이후[t]:>12,} {증분:>+10,}")
    곁기록["유량"] = {"백오프": getattr(c, "backoff_횟수", 0)}
    print(f"\n소요 {time.monotonic() - 시작:,.0f}초 · 백오프 {getattr(c, 'backoff_횟수', 0)}회")

    게, 보, 실패 = audit.run(conn)
    _곁기록에_담는다(곁기록, 게, 보)
    print(f"\n{'게이트':<46} 값")
    print("─" * 60)
    for 번호, 이름, v, ok in 게:
        print(f"{번호:<4} {이름:<42} {v:>7,}  {'🟢' if ok else '🔴'}")
    for 번호, 이름, v in 보:
        print(f"{번호:<4} {이름:<42} {v}")
    # ⚠️ **판정을 DB 에 한 줄로 남긴다.** 세션 시작 훅은 감사를 돌릴 수 없고(게이트 하나가
    #    백만 행을 훑는다) 종료코드는 이 프로세스와 함께 사라진다. 그래서 나이만 보고
    #    "최종 적재 오늘"이라고 안내하는데, **게이트가 빨간 실행도 적재 시각은 갱신한다.**
    with db.트랜잭션(conn):
        db.상태기록(conn, "감사", "전체", "완료", 실패,
                   None if not 실패 else
                   " · ".join(f"{번호}={v:,}" for 번호, _, v, ok in 게 if not ok))
    conn.close()

    # ⚠️ **게이트 위반(1)과 중단(2)을 같은 빨간불로 만들지 마라.** 전자는 사람이 봐야 하고
    #    후자는 다음 실행이 자동으로 이어받는다. 둘이 같아 보이면 곧 둘 다 무시하게 된다.
    곁기록["종류"] = "게이트" if 실패 else ("정상" if 끝냄 else "중단")
    return 1 if 실패 else (0 if 끝냄 else 2)


if __name__ == "__main__":
    raise SystemExit(_main())
