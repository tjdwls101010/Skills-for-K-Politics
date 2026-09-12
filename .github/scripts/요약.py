#!/usr/bin/env python3
"""수집 실행 하나를 **읽히는 마크다운 한 장**으로 접는다. 국회·법령이 같은 것을 쓴다.

자동 수집은 아무도 안 보고 있다. **사흘 뒤에 "언제부터 이상해졌나"에 답할 수 있어야 하므로**
실행 중이 아니라 실행 후에 읽힐 것을 전제로 쓴다. 종전에는 파이썬이 찍은 고정폭 표를
코드펜스에 그대로 부었는데, 한글이 2칸을 먹어 정렬이 무너지고 게이트 스물몇 줄 가운데
🔴 한둘을 눈으로 찾아야 했다 — **읽히지 않는 요약은 없는 요약이다.**

⚠️ **표준 라이브러리만 쓰고 `uv run` 으로 부르지 않는다.** 워크플로의 모든 `uv run` 줄은
   `caffeinate` 아래 있어야 한다는 검사가 있고(`tests/harness/test_수집_워크플로.py`,
   근거는 자다 죽은 러너 실측 3회), 이 스텝은 몇 밀리초짜리라 잠을 막을 이유가 없다.
   `watchdog.sh` 가 이미 `python3` 를 직접 부른다.

⚠️ **절대 0 아닌 것으로 끝나지 않는다.** 요약이 빨개지면 진짜 판정 옆에 **두 번째
   빨간불**이 생기고, 사람은 어느 쪽이 진짜인지 매번 다시 판단하게 된다. 최종 판정은
   `판정 전파` 스텝이 `if: always()` 로 따로 낸다 — 여기서 죽어도 그건 안 사라진다.

⚠️ **`$GITHUB_STEP_SUMMARY` 를 여기서 열지 않는다.** stdout 으로 내고 워크플로가 `>>`
   로 붙인다 — 그래야 검사가 이 스크립트를 그냥 부를 수 있다.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

KST = _dt.timezone(_dt.timedelta(hours=9))

# ⚠️ **종료코드가 아니라 `종류` 가 문구를 소유한다.** 법령의 인증 실패도 `1` 인데 그때
#    감사는 아예 안 돌았다 — `1` 을 "게이트 위반, 사람이 봐야 한다"로 읽으면 요약이
#    거짓말을 한다. 그리고 락에 막힌 3 은 실패가 아닌데 코드만 보면 그것을 모른다.
문구 = {
    "정상": "🟢 정상",
    "게이트": "🔴 **게이트 위반 — 사람이 봐야 한다**",
    "락": "⚪ **건너뛰었다 — 이미 도는 실행이 있어 락을 못 잡았다.** 오늘 원천에서 받은 것이 없다",
    "중단": "⚠️ **중단됐다. 다음 실행이 이어받는다** — 사람이 할 일은 없다",
    "인증": "🔴 **인증 실패 — 즉시 중단했다.** 열쇠를 확인해라. 다음 실행도 똑같이 실패한다",
    "거부": "🔴 **인자가 어긋나 아무것도 안 했다** — 손으로 부른 실행이다",
    "예외": "🔴 **뜻밖의 예외로 죽었다** — 아래 로그를 직접 봐라",
}


def 한줄(v) -> str:
    """줄바꿈만 편다. **산문(해석·경고)은 여기까지만 손댄다** — 표 밖이라 `|` 가 무해하다."""
    return str(v).replace("\n", " ")


def 칸(v) -> str:
    """표 한 칸. `|` 와 줄바꿈이 표를 깨뜨리므로 여기서 막는다.

    ⚠️ **이스케이프의 임자는 여기 하나다.** 값을 만드는 쪽에서도 escape 하면 `c|d` 가
    `c\\|d` 로 두 번 먹혀 화면에 백슬래시가 그대로 보인다 (실측).
    """
    return 한줄(v).replace("|", "\\|")


def 수(v) -> str:
    """숫자에 천 단위 쉼표만. **escape 하지 않는다** — 그건 표 작성기가 한다."""
    return f"{v:,}" if isinstance(v, int) else str(v)


def 펜스(경로: str | None, 줄수: int, 없을때: str) -> None:
    """원문 꼬리를 그대로 붙인다. **구조화할 것이 없는 갈래에서는 날것이 맞다.**"""
    글 = ""
    try:
        if 경로:
            글 = Path(경로).read_text(errors="replace")
    except OSError:
        글 = ""
    print("\n```")
    print("\n".join(글.splitlines()[-줄수:]) if 글.strip() else 없을때)
    print("```")


def 표(제목: str, 줄들: list[tuple], 머리: tuple[str, ...] | None = None) -> None:
    if not 줄들:
        return
    print(f"\n### {제목}\n")
    머리 = 머리 or ("", "")
    print("| " + " | ".join(머리) + " |")
    print("|" + "|".join(["---"] * len(머리)) + "|")
    for 줄 in 줄들:
        print("| " + " | ".join(칸(v) for v in 줄) + " |")


def 접은표(제목: str, 줄들: list[tuple], 머리: tuple[str, ...]) -> None:
    """⚠️ **초록은 접는다.** 통과한 스물몇 줄이 펼쳐져 있으면 빨간 한 줄이 그 안에
    묻히고, 그게 요약이 안 읽히던 이유다. 그래도 지우지는 않는다 — 추이를 보는 자리다."""
    if not 줄들:
        return
    print(f"\n<details><summary>{제목}</summary>\n")
    print("| " + " | ".join(머리) + " |")
    print("|" + "|".join(["---"] * len(머리)) + "|")
    for 줄 in 줄들:
        print("| " + " | ".join(칸(v) for v in 줄) + " |")
    print("\n</details>")


def 항목들(r: dict, 키: str) -> list[dict]:
    """`r[키]` 를 dict 리스트로 추린다.

    ⚠️ **JSON 이 파싱된다고 모양이 맞는 것은 아니다.** 곁기록을 쓰는 쪽이 중간에 죽거나
    사람이 손으로 고치면 배열 원소가 `null` 인 반쪽짜리가 남을 수 있고, 그때 예외가
    밖으로 새면 **진짜 판정 옆에 두 번째 빨간불**이 생긴다. 여기서 조용히 걸러 낸다 —
    걸러진 만큼은 어차피 그릴 값이 없는 항목이다.
    """
    값 = r.get(키)
    return [x for x in 값 if isinstance(x, dict)] if isinstance(값, list) else []


def 그린다(r: dict, 로그: str | None) -> None:
    종류 = r.get("종류", "예외")
    게이트 = 항목들(r, "게이트")
    빨강 = [g for g in 게이트 if not g.get("ok")]

    if 종류 == "게이트" and 빨강:
        print(f"🔴 **게이트 {len(빨강)}건 위반 — 사람이 봐야 한다**")
    else:
        print(문구.get(종류, 문구["예외"]))

    # ⚠️ **부분 수신은 게이트가 없다.** A25 는 '건너뜀' 만 세고(흔한 날에 빨개지면
    #    늑대를 부르므로 일부러 그렇게 뒀다), 그래서 정리가 며칠째 멈춰 있어도 판정은
    #    초록이다. **이 줄이 그 상태를 사람 눈에 넣는 유일한 자리다.**
    for b in 항목들(r, "보고"):
        if b.get("번호") == "R22" and str(b.get("값") or "").strip() not in ("", "없음"):
            print(f"\n> ⚠️ 정리가 부분 수신으로 멈춰 있다 — {한줄(b['값'])}"
                  f" (게이트는 이걸 안 잡는다)")

    이번 = [(단.get("이름"), " · ".join(f"{k} {수(v)}"
                                     for k, v in (단.get("수치") or {}).items()))
          for 단 in 항목들(r, "단계")]
    이번 += [(t.get("이름"), f"{수(t.get('이전'))} → {수(t.get('이후'))} "
                          f"({t.get('증분', 0):+,})") for t in 항목들(r, "표")]
    유량 = r.get("유량") if isinstance(r.get("유량"), dict) else {}
    if 유량:
        # ⚠️ **라벨을 손으로 적지 마라.** 두 수집기가 세는 것이 다르다 — 법령은 요청과
        #    백오프를, 국회는 백오프만 센다. 고정 라벨을 쓰면 「요청 · 백오프 | 백오프 0」
        #    처럼 있지도 않은 값을 약속하게 된다(실측).
        이번.append(("유량", " · ".join(f"{k} {수(v)}" for k, v in 유량.items())))
    if r.get("소요초") is not None:
        이번.append(("소요", f"{r['소요초'] / 60:.1f}분"))
    표("이번 실행", 이번)

    if not r.get("감사실행"):
        print("\n감사는 돌기 전에 끝났다 — 게이트 표가 없는 것이 정상이다.")
        펜스(로그, 40, "(수집 로그가 없다)")
        return

    표("🔴 위반한 게이트", [(g.get("번호"), g.get("이름"), 수(g.get("값"))) for g in 빨강],
      ("#", "게이트", "값"))
    # ⚠️ **게이트 이름은 "무엇을 셌나"이지 "그래서 무슨 일이 났나"가 아니다.**
    #    이 줄은 빨간 순간에만, 공짜로 읽힌다.
    for g in 빨강:
        if g.get("해석"):
            print(f"\n> **{g['번호']}** — {한줄(g['해석'])}")

    초록 = [g for g in 게이트 if g.get("ok")]
    접은표(f"통과한 게이트 {len(초록)}건", [(g.get("번호"), g.get("이름")) for g in 초록],
         ("#", "게이트"))
    접은표("보고값 (추이를 본다)",
         [(b.get("번호"), b.get("이름"), 수(b.get("값"))) for b in 항목들(r, "보고")],
         ("#", "보고값", "값"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--제목", required=True)
    ap.add_argument("--결과", help="수집기가 남긴 result.json")
    ap.add_argument("--preflight", default="success", help="preflight 스텝의 outcome")
    ap.add_argument("--verify", default="0", help="verify 스텝의 종료코드")
    ap.add_argument("--preflight-로그", dest="preflight_로그", default="preflight.txt")
    ap.add_argument("--verify-로그", dest="verify_로그", default="verify.txt")
    ap.add_argument("--로그", default="collect.txt", help="수집 stdout 사본")
    a = ap.parse_args(argv)

    print(f"## {a.제목} · {_dt.datetime.now(KST):%Y-%m-%d %H:%M KST}\n")

    # ⚠️ **앞의 두 갈래에서는 `result.json` 이 애초에 없다** — 수집 스텝이 안 돌았다.
    #    그것만 읽으려 들면 요약이 매번 "곁기록이 없다"로 끝나 원인을 못 보여준다.
    if a.preflight != "success":
        print("🔴 **DB 경로 점검 실패 — 수집을 시작하지 않았다**")
        펜스(a.preflight_로그, 40, "(DB 점검 로그가 없다)")
        return 0
    if str(a.verify) != "0":
        print("🔴 **검증 실패 — 원천이 바뀌었을 수 있다. 수집을 건너뛰었다.**"
              " 계획 문서를 먼저 고쳐라")
        펜스(a.verify_로그, 40, "(검증 로그가 없다)")
        return 0

    try:
        r = json.loads(Path(a.결과).read_text())
        if not isinstance(r, dict):
            raise ValueError("JSON 최상위가 객체가 아니다")
    except Exception as e:                                    # noqa: BLE001
        # ⚠️ **여기서 예외를 밖으로 내지 마라.** 곁기록이 없는 날은 대개 실행이
        #    SIGKILL·러너 유실·잡 타임아웃으로 죽은 날이고, 그날이야말로 로그가 필요하다.
        print(f"⚠️ **수집 결과를 읽지 못했다** — `{한줄(e)}`. 아래 로그를 직접 봐라.")
        펜스(a.로그, 60, "(수집 로그가 없다)")
        return 0

    # ⚠️ **여기가 마지막 안전망이다.** 위의 모양 방어를 다 통과한 뒤에도 그리다 죽을
    #    수 있고(값의 타입 조합은 끝이 없다), 그때 예외가 밖으로 새면 요약 스텝이
    #    빨개져 **진짜 판정 옆에 두 번째 빨간불**이 선다. 요약은 판정하는 자리가 아니다.
    try:
        그린다(r, a.로그)
    except Exception as e:                                    # noqa: BLE001
        print(f"\n⚠️ **요약을 그리다 죽었다** — `{한줄(e)}`. 아래 로그를 직접 봐라.")
        펜스(a.로그, 60, "(수집 로그가 없다)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
