#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""의안 · 의안심사 · 발의자 · 표결 · 대안.

**의안번호가 종류 구분 없이 연속된 정수라, 전수를 담아야 "번호에 구멍이 없다 = 누락이 없다"가
성립한다.** 법률안만 담으면 구멍이 비법률안인지 수집 실패인지 영원히 구분되지 않는다.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import net  # noqa: E402

KST = timezone(timedelta(hours=9))

목록_API = "TVBPMBILL11"        # 법률안 전량 20,019건. 21페이지
통합_API = "ALLBILL"            # BILL_NO 완전일치. 비법률안 + 공포·이송·재의
요약_API = "BPMBILLSUMMARY"     # 제안이유 및 주요내용
소위_API = "TVBPMCONFINFO"      # 소위 심사정보 16,342행

법사위 = "법제사법위원회"


def now_kst() -> str:
    """⚠️ SQLite 의 `datetime('now')` 는 UTC 다. 9시간 어긋나므로 파이썬에서 만들어 바인딩한다."""
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


# ── TVBPMBILL11 ───────────────────────────────────────────────────────────────
# `의안.제안자구분` 의 CHECK 과 **반드시 이 한 목록에서 나온다.** 두 곳에 적으면
# 한쪽만 고치는 날이 오고, 그날 수집이 IntegrityError 로 죽는다.
제안자구분값 = ("의원", "위원장", "정부", "의장", "기타")


def 제안자구분(값) -> str | None:
    """CHECK 밖 값이면 `None` 을 준다 — **의안은 넣고 이 칸만 비운다.**

    ⚠️ **`표결결과` 처럼 행을 통째로 빼면 안 된다.** 의안이 안 들어오면 의안번호에
    구멍이 생기고, 그러면 A1 완결성 게이트가 "원천에 없는 번호"와 "우리가 거부한 번호"를
    영원히 구별하지 못한다. 호출자가 `수집실패` 에 '막힘' 으로 남긴다.
    """
    return 값 if 값 in 제안자구분값 else None


def 의안행(row: dict) -> dict:
    """`TVBPMBILL11` 한 행 → `db.upsert_의안(…, 'TVBPMBILL11', …)` 에 넣을 dict.

    ⚠️ **`의안종류` 는 응답 필드가 아니라 상수다.** 이 API 는 법률안만 준다. 이걸 `ALLBILL`
    에만 맡기면 그 API 를 2,005건에만 부르므로 **계류 법률안 1만 4천 건이 영구히 NULL** 이 되고,
    재수집 조건 `의안종류 IS NULL`("아직 안 받은 비법률안")도 함께 무너진다.

    ⚠️ **`PASS_GUBUN` 을 담지 않는다.** `PROC_RESULT_CD IS NULL` 여부와 20,019건 전부
    일치하는 순수 중복이다.
    """
    return {
        "의안번호": row["BILL_NO"],
        "의안ID": row["BILL_ID"],
        "의안명": row["BILL_NAME"],
        "의안종류": "법률안",
        "제안자구분": 제안자구분(row.get("PROPOSER_KIND")),
        "제안일": row.get("PROPOSE_DT"),
        "소관위원회": row.get("CURR_COMMITTEE") or None,
        "처리결과": row.get("PROC_RESULT_CD"),
    }


def 심사행들(row: dict) -> list[dict]:
    """`TVBPMBILL11` 한 행 → 소관위·법사위·본회의 심사 행들. **사건이 없으면 행도 없다.**

    ⚠️ **소관위 처리일은 `CMT_PROC_DT` 다. `COMMITTEE_PROC_DT` 가 아니다.**
    이름이 더 자연스럽게 읽히는 쪽이 함정이다 — 실측(페이지20 1,000행): 소관위 처리결과가
    있는 459행 중 **448행에서 `COMMITTEE_PROC_DT` 가 NULL** 이고 `CMT_PROC_DT` 에만
    날짜가 있다. 잘못 고르면 소관위 처리일의 97%가 NULL 이 되는데 **에러가 안 난다.**
    """
    행들: list[dict] = []

    위원회 = row.get("CURR_COMMITTEE") or None
    소관위 = {
        "단계": "소관위",
        "위원회명": 위원회,
        "회부일": row.get("COMMITTEE_DT"),
        "상정일": row.get("CMT_PRESENT_DT"),
        "처리일": row.get("CMT_PROC_DT"),
        "처리결과": row.get("CMT_PROC_RESULT_CD"),
    }
    # 회부 전이면 CURR_COMMITTEE 가 NULL 이다(실측 45건/20,019). 위원회명이 NOT NULL 은
    # 아니지만, 위원회도 날짜도 없는 행은 아무것도 말하지 않으므로 만들지 않는다.
    if 위원회 or any(소관위[k] for k in ("회부일", "상정일", "처리일", "처리결과")):
        행들.append(소관위)

    법사위행 = {
        "단계": "법사위",
        # ⚠️ 원천이 법사위 칸을 안 준다 — 언제나 법제사법위원회이므로 우리가 채운다.
        "위원회명": 법사위,
        "회부일": row.get("LAW_SUBMIT_DT"),
        "상정일": row.get("LAW_PRESENT_DT"),
        "처리일": row.get("LAW_PROC_DT"),
        "처리결과": row.get("LAW_PROC_RESULT_CD"),
    }
    if any(법사위행[k] for k in ("회부일", "상정일", "처리일", "처리결과")):
        행들.append(법사위행)

    본회의 = {
        "단계": "본회의",
        # ⚠️ 원천에 위원회 칸도 회부·상정 칸도 없다. NULL 이 정상이다.
        "위원회명": None,
        "회부일": None,
        "상정일": None,
        "처리일": row.get("PROC_DT"),
        "처리결과": row.get("PROC_RESULT_CD"),
    }
    if 본회의["처리일"] or 본회의["처리결과"]:
        행들.append(본회의)
    return 행들


# ── ALLBILL ───────────────────────────────────────────────────────────────────
def split_allbill(rows) -> tuple[dict, dict | None]:
    """`ALLBILL` 응답 → `(본체, 재의|None)`.

    거부권이 행사된 법안은 `PRC_`(또는 `ARC_`)와 `GOV_` 가 **접미부가 같은 채로** 함께 온다.

    ⚠️ **`row[0]` 을 집지 마라 — `GOV_` 가 먼저 올 수 있다**(실측 2200851). `GOV_` 를 본체로
    저장하면 `의안ID` 가 그걸로 굳고, 그러면 **표결 조회가 `INFO-200` 을 돌려주며 공포 3종도
    전부 NULL 이 된다.** 계획 세션이 실제로 이 함정을 밟아 "거부권 법안은 표결 API 에 없다"는
    틀린 결론을 적을 뻔했다.

    ⚠️ **`GOV_` 행의 존재 자체가 "정부가 재의를 요구했다"** 는 뜻이다.
    """
    본체 = next((r for r in rows if r["BILL_ID"].startswith(("PRC_", "ARC_"))), None)
    재의 = next((r for r in rows if r["BILL_ID"].startswith("GOV_")), None)
    if 본체 is None:
        raise ValueError(
            f"ALLBILL 응답에 본체(PRC_/ARC_) 행이 없다: {[r['BILL_ID'][:4] for r in rows]}"
        )
    return 본체, 재의


def allbill_의안행(본체: dict) -> dict:
    """`ALLBILL` 본체 행 → `의안` dict.

    ⚠️ **`PROM_LAW_NM` 을 믿지 마라.** 일괄개정에서는 공포일이 있으면서 이 값이 비어 온다
    (2201193 은 10개 법률을 공포했는데 null). **"공포법률명이 없다"가 "공포 안 됐다"가 아니다.**
    """
    return {
        "의안번호": 본체["BILL_NO"],
        "의안ID": 본체["BILL_ID"],
        "의안명": 본체.get("BILL_NM"),
        "의안종류": 본체.get("BILL_KND"),
        "제안자구분": 제안자구분(본체.get("PPSR_KND")),
        "제안일": 본체.get("PPSL_DT"),
        # ⚠️ 소관위원회는 **새 행을 만들 때만** 쓴다(`db.의안_원천` 의 생성/갱신 분리).
        #    `GOV_` 행의 JRCMIT_NM 은 '본회의' 이고, 이 API 는 10%에만 부르므로
        #    나머지와 규칙이 달라진다. 정본은 TVBPMBILL11.CURR_COMMITTEE 다.
        "소관위원회": 본체.get("JRCMIT_NM") or None,
        "처리결과": 본체.get("RGS_CONF_RSLT") or None,
        "정부이송일": 본체.get("GVRN_TRSF_DT"),
        "공포일": 본체.get("PROM_DT"),
        "공포법률명": 본체.get("PROM_LAW_NM") or None,
        "공포번호": 본체.get("PROM_NO"),
    }


def 재의행(gov: dict) -> dict:
    """`GOV_` 행 → `의안심사` 의 `'재의'` 단계.

    **이것이 거부권의 유일한 기록이다.** `의안.처리결과` 는 본회의 **최초** 심의결과라
    재의를 모른다 — 2208496 은 거기서 '원안가결'인데 실제로는 거부권 후 부결돼 법이 되지 않았다.
    """
    return {
        "단계": "재의",
        "위원회명": None,
        "회부일": None,
        "상정일": gov.get("RGS_PRSNT_DT"),
        "처리일": gov.get("RGS_RSLN_DT"),
        "처리결과": gov.get("RGS_CONF_RSLT"),
    }


# ── TVBPMCONFINFO ─────────────────────────────────────────────────────────────
def 소위행(row: dict) -> dict:
    """`TVBPMCONFINFO` 한 행 → `의안심사` 의 `'소위'` 단계.

    ⚠️ **`SUB_COMMITTEE_NAME` 이 52% 비어 있다**(8,556/16,342). 소위인 것은 아는데 어느
    소위인지 원천이 안 준다. **상위 위원회명으로 대신 채우지 마라** — 소위가 아닌 것처럼 보인다.
    NULL 이 정직한 값이다.
    """
    상위 = (row.get("COMMITTEE_NAME") or "").strip()
    소위명 = (row.get("SUB_COMMITTEE_NAME") or "").strip()
    return {
        "단계": "소위",
        "위원회명": f"{상위} {소위명}" if (상위 and 소위명) else None,
        "회부일": row.get("SUBMIT_DT"),
        "상정일": row.get("PRESENT_DT"),
        "처리일": row.get("PROC_DT"),
        "처리결과": row.get("PROC_RESULT_CD"),
    }


# ── 발의자 ────────────────────────────────────────────────────────────────────
발의자_API = "nzmimeepazxkubdpn"   # 의원 발의 법률안 18,705행. 19페이지
제안자_API = "BILLINFOPPSR"        # 의안별 보정. 비법률안 의원 제안자에 쓴다
표결집계_API = "ncocpgfiaoituanbr"  # 22대 1,656건
표결명단_API = "nojepdqqaweusdfbi"  # BILL_ID 필수
표결결과값 = ("찬성", "반대", "기권", "불참")


def _코드들(값: str | None) -> list[str]:
    return [x.strip() for x in (값 or "").split(",") if x.strip()]


def parse_proposers(row: dict) -> tuple[list[tuple[str, str]], bool]:
    """`nzmimeepazxkubdpn` 한 행 → `([(의원코드, 역할)], 정합성ok)`.

    ⚠️ **대표발의가 한 명이라고 가정하지 마라.** `RST_MONA_CD` 자체가 쉼표로 여럿을 준다
    (1,000행 표본 중 4건). 첫 번째만 대표로 잡으면 `역할='대표발의'` 가 2명 이상인 의안이
    **0건이 되고, 0건은 에러가 아니라 그대로 답이 된다.**

    ⚠️ **정합성 검사는 수집 시점에만 가능하다.** DB 에 표시용 제안자 문자열을 담지 않으므로
    사후 감사로는 대조할 대상이 없다 — `PROPOSER` 의 `등 N인` 과 코드 수가 어긋나면
    **저장하지 말고 원장에 남겨야 한다.**
    """
    대표, 공동 = _코드들(row.get("RST_MONA_CD")), _코드들(row.get("PUBL_MONA_CD"))
    역할: dict[str, str] = {}
    for 코드 in 공동:
        역할[코드] = "공동발의"
    for 코드 in 대표:
        역할[코드] = "대표발의"  # 같은 사람이 둘 다면 대표가 이긴다 (PK 에 역할이 없다)

    ok = True
    if m := re.search(r"등\s*(\d+)\s*인", row.get("PROPOSER") or ""):
        ok = int(m.group(1)) == len(대표) + len(공동)
    return [(c, 역할[c]) for c in 대표 + [c for c in 공동 if c not in 대표]], ok


# ── 표결 ──────────────────────────────────────────────────────────────────────
def 표결집계행(row: dict) -> dict:
    """**이 테이블에 행이 있다 = 그 의안은 본회의 표결을 거쳤다.**

    ⚠️ **집계를 명단에서 유도하지 마라.** 명단이 집계와 어긋나는 의안이 실재한다
    (2220257: 집계 찬성 175 / 명단 찬성 166). `GROUP BY` 로 만든 집계는 조용히 틀린다.
    """
    def 수(k: str) -> int | None:
        v = row.get(k)
        return int(v) if v not in (None, "") else None

    return {
        "의안번호": row["BILL_NO"],
        "의결일": row.get("PROC_DT"),
        "재적수": 수("MEMBER_TCNT"),
        "투표수": 수("VOTE_TCNT"),
        "찬성수": 수("YES_TCNT"),
        "반대수": 수("NO_TCNT"),
        "기권수": 수("BLANK_TCNT"),
    }


def 표결행(row: dict) -> tuple[str, str, str] | None:
    """의원별 표결. **CHECK 밖 값이면 `None` 을 준다 — 그 행만 빼고 실행은 계속된다.**

    틀린 값이 들어오는 것보다 안 들어오는 것이 낫다. 호출자가 `수집실패` 에 `막힘` 으로 남긴다.
    """
    결과 = (row.get("RESULT_VOTE_MOD") or "").strip()
    코드 = (row.get("MONA_CD") or "").strip()
    if 결과 not in 표결결과값 or not 코드:
        return None
    return (row["BILL_NO"], 코드, 결과)


# ── 대안 ──────────────────────────────────────────────────────────────────────
def parse_anbill(html: str, 자기번호: str) -> set[str]:
    """likms `anBillInfo.do` 응답 → 흡수된 원안의 의안번호 집합.

    ⚠️ **`<tbody class="anBill_body">` 가 비어 올 수 있다** — 표가 서버가 채우는 부분과
    클라이언트가 그리는 부분으로 섞여 있다. 그래서 DOM 을 파고들지 않고 응답 전체를
    정규식으로 긁은 뒤 **자기 번호를 제외**한다.

    ⚠️ **0건이면 성공으로 넘기지 마라.** 대안이면 흡수한 원안이 반드시 하나 이상이다.
    """
    번호들 = set(re.findall(r"\b2[12]\d{5}\b", html)) - {자기번호}
    if not 번호들:
        raise ValueError(f"대안 {자기번호}: 흡수된 의안 파싱이 0건이다")
    return 번호들


# ── 수집 ──────────────────────────────────────────────────────────────────────
def 위원회_해소(conn, 값: dict, 행들: list[dict]) -> None:
    """위원회명을 **`upsert_위원회` 가 실제로 저장한 표기로** 바꿔치기한다.

    ⚠️ **원천이 준 이름을 그대로 자식 행에 넣으면 FK 위반이다.** 두 원천이 같은 위원회를
    다르게 적는다 — 회의록 목록 API 는 `'기후위기특별위원회'`, `TVBPMBILL11.CURR_COMMITTEE`
    는 `'기후위기 특별위원회'`(공백). `upsert_위원회` 는 공백·중점을 지운 형태로 기존 행을
    찾아 **저장된 이름을 돌려주는데, 그 반환값을 안 쓰면 아무 소용이 없다.**

    실측: 22대 의안 632번째(2219908)에서 정확히 이 이유로 FK 가 터졌다. **FK 가 꺼져 있었다면
    조용히 고아 행이 되고 그 위원회의 의안 조회가 영원히 0건이었을 것이다** — 그래서 연결
    헬퍼가 매번 `PRAGMA foreign_keys=ON` 을 거는 것이고, 그걸 검사하는 테스트가 S13 이다.
    """
    if 값.get("소관위원회"):
        값["소관위원회"] = db.upsert_위원회(conn, 값["소관위원회"])
    for r in 행들:
        if r.get("위원회명"):
            r["위원회명"] = db.upsert_위원회(conn, r["위원회명"])


def collect_목록(conn, c: net.Client, 로그=print) -> dict[str, int]:
    """2단계 — `TVBPMBILL11` 전량. **매 실행 전량이다**(21요청, 1분).

    여기서 의안의 상태 변화가 전부 잡힌다. "무엇이 바뀌었나"를 추론할 필요가 없다.
    """
    수치 = {"의안": 0, "심사": 0}
    번호들: set[str] = set()
    for row in c.all_pages(목록_API, AGE=22):
        번호 = row["BILL_NO"]
        번호들.add(번호)
        with db.트랜잭션(conn):
            값, 행들 = 의안행(row), 심사행들(row)
            위원회_해소(conn, 값, 행들)          # ← 반환값을 쓰는 것이 요점이다
            db.upsert_의안(conn, "TVBPMBILL11", 값)
            db.replace_의안심사(conn, 번호, ("소관위", "법사위", "본회의"), 행들)
            conn.execute(
                "UPDATE 의안 SET 수집시각=? WHERE 의안번호=?", (now_kst(), 번호)
            )
            db.clear_failure(conn, "의안상세", 번호)
            # ⚠️ **`clear_failure` 뒤여야 한다** — 앞에 남기면 같은 트랜잭션이 방금 지운다.
            #    NULL 로 조용히 넘어가면 새 라벨이 생긴 날 제안 주체가 통째로 비는데
            #    아무도 모른다. '막힘' 은 사람이 고칠 때까지 감사가 계속 가리킨다.
            if row.get("PROPOSER_KIND") and 값["제안자구분"] is None:
                db.record_failure(conn, "의안상세", 번호, "막힘",
                                  f"check:제안자구분={row['PROPOSER_KIND']!r}")
        수치["의안"] += 1
        수치["심사"] += len(행들)
        if 수치["의안"] % 5000 == 0:
            로그(f"  의안 {수치['의안']:,}건")
    수치["번호들"] = 번호들  # type: ignore[assignment]
    로그(f"의안 목록 {수치['의안']:,}건 · 심사 {수치['심사']:,}행")
    return 수치


def 차집합(번호들: set[str], c: net.Client, 로그=print) -> list[int]:
    """숫자 범위에서 법률안 번호를 뺀 나머지 + **위로 탐침한 꼬리**.

    ⚠️ **상한을 법률안 최대 번호로 잡으면 꼬리를 통째로 놓친다.** 최신 법률안 뒤에 비법률안만
    연속 접수되면 그 구간이 범위 밖이라 `ALLBILL` 을 부르지도 않고, **A1(구멍 수)의 모집단에서도
    빠져 게이트가 초록이다.** `INFO-200` 이 10회 연속 나올 때까지 위로 탐침한다.
    """
    숫자 = sorted(int(n) for n in 번호들 if n.isdigit())
    if not 숫자:
        # ⚠️ **원천이 빈손인 날 여기서 `IndexError` 로 죽었다.** 트레이스백과 함께 종료코드
        #    1이 나가는데 워크플로는 1을 「게이트 위반 — 사람이 봐야 한다」로 읽고, 무엇보다
        #    **죽으면 뒤의 감사가 아예 안 돌아** 무엇이 이상한지도 안 남는다. 빈손을 잡으라고
        #    있는 게이트가 빈손 때문에 실행되지 않는 모양이다. 탐침할 기준이 없으니
        #    조용히 물러나고, 판정은 감사(A13)에 맡긴다.
        로그("의안 번호가 하나도 없다 — 탐침할 기준이 없어 보완을 건너뛴다")
        return []
    하한, 상한 = 숫자[0], 숫자[-1]
    빈칸 = 0
    후보 = 상한 + 1
    꼬리: list[int] = []
    while 빈칸 < 10:
        rows, _ = c.json(통합_API, BILL_NO=str(후보))
        if rows:
            꼬리.append(후보)
            빈칸 = 0
        else:
            빈칸 += 1
        후보 += 1
    if 꼬리:
        로그(f"  꼬리 탐침: {상한} 위로 {len(꼬리)}건 발견 (최대 {꼬리[-1]})")
    나머지 = [n for n in range(하한, 상한 + 1) if str(n) not in 번호들]
    return 나머지 + 꼬리


def collect_보완(conn, c: net.Client, 번호들: set[str], 로그=print, 표본: int | None = None) -> dict:
    """3단계 — `ALLBILL` 로 비법률안·공포·이송·재의를 채운다.

    세 부류에만 부른다: **차집합**(비법률안, 의안종류가 아직 NULL) + **공포가 아직 안 온 가결 의안** + **처리결과가 NULL 인 비법률안**.

    ⚠️ **조건이 `정부이송일 IS NULL` 이면 안 된다.** 정부이송은 공포보다 **먼저** 일어나므로
    이송일이 채워지는 순간 재조회가 멈추고 **그 뒤에 오는 공포일과 `GOV_` 재의 행을 영구히
    놓친다.** 2200851 은 이송(2025-04-17)과 공포(2025-04-22) 사이가 떨어져 있고, 수정가결
    (2024-12-26)부터 공포까지는 약 4개월이다.
    """
    대상 = [str(n) for n in 차집합(번호들, c, 로그)]
    대상 += [
        r[0]
        for r in conn.execute(
            "SELECT 의안번호 FROM 의안"
            # 공포는 법률안에만 있다 — 비법률안까지 걸면 가결된 결의안이 매일 다시 불린다.
            " WHERE (의안종류 = '법률안' AND 처리결과 IN ('원안가결','수정가결') AND 공포일 IS NULL)"
            " OR (의안종류 IS NOT NULL AND 의안종류 <> '법률안' AND 처리결과 IS NULL)"
        )
    ]
    대상 = list(dict.fromkeys(대상))
    if 표본:
        대상 = 대상[:표본]
    수치 = {"보완": 0, "없음": 0, "재의": 0}
    진행 = net.진행자(로그, "의안 보완", len(대상), c)
    for i, 번호 in enumerate(대상, 1):
        진행(i)
        rows, _ = c.json(통합_API, BILL_NO=번호)
        if not rows:
            # ⚠️ INFO-200 은 실패가 아니다 — 아직 존재하지 않는 번호가 정상적으로 그렇게 답한다.
            #    '없음' 으로 남겨야 A1(구멍 수)이 이걸 구멍에서 제외한다.
            with db.트랜잭션(conn):
                db.record_failure(conn, "의안상세", 번호, "없음", "ALLBILL: INFO-200")
            수치["없음"] += 1
            continue
        본체, 재의 = split_allbill(rows)
        with db.트랜잭션(conn):
            값 = allbill_의안행(본체)
            있음 = conn.execute("SELECT 의안종류 FROM 의안 WHERE 의안번호=?", (번호,)).fetchone()
            if not 있음 and 값.get("소관위원회"):
                값["소관위원회"] = db.upsert_위원회(conn, 값["소관위원회"])
            elif 있음:
                값.pop("소관위원회", None)   # 갱신 범위 밖이다
                값.pop("의안ID", None)
                값.pop("의안명", None)
                값.pop("제안자구분", None)
                값.pop("제안일", None)
                # ⚠️ 법률안의 처리결과 정본은 `TVBPMBILL11` 이다(표본 20건 일치). 여기서 덮으면 원천이
                #    비어 온 날 가결이 NULL 로 되돌아간다 — 비법률안에만, 값이 있을 때만 쓴다.
                if 있음[0] == "법률안" or 값.get("처리결과") is None:
                    값.pop("처리결과", None)
            db.upsert_의안(conn, "ALLBILL", 값)
            db.replace_의안심사(
                conn, 번호, ("재의",), [재의행(재의)] if 재의 else []
            )
            conn.execute("UPDATE 의안 SET 수집시각=? WHERE 의안번호=?", (now_kst(), 번호))
            db.clear_failure(conn, "의안상세", 번호)
            # ⚠️ **`clear_failure` 뒤여야 한다** — 앞에 남기면 같은 트랜잭션이 방금 지운다.
            #    `not 있음` 인 것도 요점이다: 이미 있는 의안이면 제안자구분이 갱신 범위
            #    밖이라 애초에 안 쓰이므로, 그때 원장에 남기면 고칠 수 없는 빨강이 된다.
            if 본체.get("PPSR_KND") and not 있음 and 값.get("제안자구분") is None:
                db.record_failure(conn, "의안상세", 번호, "막힘",
                                  f"check:제안자구분={본체['PPSR_KND']!r}")
        수치["보완"] += 1
        수치["재의"] += bool(재의)
    로그(f"의안 보완 {수치['보완']:,}건 (재의 {수치['재의']} · 원천에 없음 {수치['없음']})")
    return 수치


def collect_소위(conn, c: net.Client, 로그=print) -> dict:
    """5단계 — `TVBPMCONFINFO` 전량(17페이지). **`AGE` 만으로 열거된다.**

    ⚠️ **DELETE 를 `단계='소위'` 로 좁힌다.** 조건 없이 지우면 이 패스가 2단계(소관위·법사위·
    본회의)와 3단계(재의) 행을 날린다. 소위 심사가 법률안의 79%에 걸리므로 그 79%가 본회의
    행을 잃는데, **A6 게이트는 본회의 행이 아예 사라진 경우를 검사하지 않아 초록불이다.**
    """
    묶음: dict[str, list[dict]] = {}
    for row in c.all_pages(소위_API, AGE=22):
        묶음.setdefault(row["BILL_NO"], []).append(소위행(row))
    붙음 = 0
    for 번호, 행들 in 묶음.items():
        if not conn.execute("SELECT 1 FROM 의안 WHERE 의안번호=?", (번호,)).fetchone():
            continue   # 22대 밖 의안. FK 가 막으므로 여기서 거른다
        with db.트랜잭션(conn):
            위원회_해소(conn, {}, 행들)
            db.replace_의안심사(conn, 번호, ("소위",), 행들)
        붙음 += 1
    로그(f"소위 심사 {sum(len(v) for v in 묶음.values()):,}행 / 고유 의안 {len(묶음):,} (적재 {붙음:,})")
    return {"소위행": sum(len(v) for v in 묶음.values()), "소위의안": 붙음}


def collect_발의자(conn, c: net.Client, 로그=print) -> dict:
    """6단계 — `nzmimeepazxkubdpn` 전량(19페이지). 의안별 호출 2만 번을 19번으로 접는다.

    ⚠️ **이 원천은 의원 발의 "법률안" 만 준다.** 비법률안 중 의원 제안분은 `BILLINFOPPSR`
    로 따로 메운다 — 안 하면 **A3 게이트(제안자구분='의원'인데 발의자 0인 의안)가 빨갛다.**
    """
    수치 = {"발의자": 0, "의안": 0, "불일치": 0, "밖의코드": 0, "빈코드": 0, "빈응답": 0}
    의원들 = {r[0] for r in conn.execute("SELECT 의원코드 FROM 의원")}
    for row in c.all_pages(발의자_API, AGE=22):
        번호 = row["BILL_NO"]
        if not conn.execute("SELECT 1 FROM 의안 WHERE 의안번호=?", (번호,)).fetchone():
            continue
        쌍, ok = parse_proposers(row)
        with db.트랜잭션(conn):
            if not ok:
                # ⚠️ 저장하지 않고 원장에 남긴다. DB 에 PROPOSER 문자열이 없어 사후 감사가 못 한다.
                db.record_failure(
                    conn, "발의자", 번호, "막힘",
                    f"등 N인 불일치: {row.get('PROPOSER')!r} vs 코드 {len(쌍)}개",
                )
                수치["불일치"] += 1
                continue
            쓸것 = [(c_, r_) for c_, r_ in 쌍 if c_ in 의원들]
            수치["밖의코드"] += len(쌍) - len(쓸것)
            if not 쓸것:
                # ⚠️ **파싱 성공과 저장 가능은 다르다.** `등 N인` 검사는 통과했는데 코드가
                #    전부 우리 의원 표 밖이면 `쓸것` 이 빈다. 그대로 `DELETE` 하면 어제까지
                #    있던 발의자가 사라지는데, 파싱이 성공했으므로 원장에도 안 남는다.
                # ⚠️ **`쌍` 이 비었는지로 가르지 마라.** `parse_proposers` 는 코드 필드가
                #    둘 다 비어도 `([], True)` 를 준다 — `등 N인` 이 없으니 정합성 검사를
                #    건너뛰고 성공으로 본다. 그러면 이 문이 열려 그대로 DELETE 가 돈다.
                # '재시도' 인 이유: 의원 표는 upsert 라 줄지 않으므로, 이건 우리가 아직
                # 모르는 코드가 왔다는 뜻이고 다음 실행의 의원 갱신이 풀 수 있다.
                db.record_failure(
                    conn, "발의자", 번호, "재시도",
                    f"아는 의원코드 0개 (원천이 준 코드 {len(쌍)}개)",
                )
                수치["빈코드"] += 1
                continue
            conn.execute("DELETE FROM 발의자 WHERE 의안번호=?", (번호,))
            conn.executemany(
                "INSERT INTO 발의자 (의안번호, 의원코드, 역할) VALUES (?,?,?)",
                [(번호, c_, r_) for c_, r_ in 쓸것],
            )
            db.clear_failure(conn, "발의자", 번호)
        수치["발의자"] += len(쓸것)
        수치["의안"] += 1

    # 비법률안 중 의원 제안분 — BILLINFOPPSR 로 메운다
    남은 = [
        r[0]
        for r in conn.execute(
            "SELECT b.의안번호 FROM 의안 b"
            " WHERE b.제안자구분='의원'"
            "   AND NOT EXISTS (SELECT 1 FROM 발의자 p WHERE p.의안번호=b.의안번호)"
        )
    ]
    진행 = net.진행자(로그, "발의자", len(남은), c)
    for i, 번호 in enumerate(남은, 1):
        진행(i)
        의안id = conn.execute("SELECT 의안ID FROM 의안 WHERE 의안번호=?", (번호,)).fetchone()[0]
        rows, _ = c.json(제안자_API, BILL_ID=의안id)
        쌍 = [
            (r["NASS_CD"], "대표발의" if (r.get("REP_DIV") or "").startswith("대표") else "공동발의")
            for r in rows
            if r.get("NASS_CD") in 의원들
        ]
        if not 쌍:
            # ⚠️ **A3 는 '발의자가 0인 의안'을 세지만 왜 0인지는 모른다.** 빈 응답이 아무
            #    흔적을 안 남기면 그 빨간불이 영영 설명되지 않고, 원장이 비어 있으니
            #    "한 번도 안 물어봄" 과 구별도 안 된다.
            with db.트랜잭션(conn):
                db.record_failure(conn, "발의자", 번호, "재시도", "제안자 응답이 0행")
            수치["빈응답"] += 1
            continue
        with db.트랜잭션(conn):
            conn.execute("DELETE FROM 발의자 WHERE 의안번호=?", (번호,))
            conn.executemany(
                "INSERT OR IGNORE INTO 발의자 (의안번호, 의원코드, 역할) VALUES (?,?,?)",
                [(번호, c_, r_) for c_, r_ in 쌍],
            )
            # 이 경로가 남길 수 있는 것은 '재시도' 뿐이다. 전량 경로가 남긴 '막힘'
            # (등 N인 불일치)은 데이터 구멍이 메워져도 그대로 가리켜야 한다.
            db.clear_failure(conn, "발의자", 번호, "재시도")
        수치["발의자"] += len(쌍)
    로그(
        f"발의자 {수치['발의자']:,}행 / 의안 {수치['의안']:,}"
        f" (등N인 불일치 {수치['불일치']} · 22대 밖 코드 {수치['밖의코드']}"
        f" · 아는 코드 0개 {수치['빈코드']} · 비법률안 보정 {len(남은)}건"
        f" 중 빈 응답 {수치['빈응답']})"
    )
    return 수치


def collect_표결(conn, c: net.Client, 로그=print, 표본: int | None = None) -> dict:
    """7·8단계 — 집계 전량(2페이지) + 집계가 있는 의안마다 명단.

    ⚠️ **명단은 `표결집계` 응답이 준 `BILL_ID` 로 묻는다. `의안.의안ID` 가 아니다.**
    같은 의안번호에 `PRC_` 가 **둘** 있는 경우가 실재한다 — 2216968 은 `TVBPMBILL11`/`ALLBILL`
    쪽이 `PRC_Y2P6Q0…`, 표결 쪽이 `PRC_I2I6S0…` 다. 의안 쪽 id 로 물으면 `INFO-200` 이 오는데
    **표결이 없는 것이 아니라 키가 틀린 것이다.** 실측: 이것 때문에 32건의 명단이 통째로 비었고,
    **A8(표결→집계 방향)은 그걸 못 잡았다.** A10 이 잡는다.

    ⚠️ 같은 이유로 `GOV_` id 로도 물으면 안 된다(계획 세션이 밟은 함정).
    """
    수치 = {"집계": 0, "명단": 0, "막힘": 0, "빈명단": 0}
    의원들 = {r[0] for r in conn.execute("SELECT 의원코드 FROM 의원")}
    이미 = {
        r[0] for r in conn.execute("SELECT DISTINCT 의안번호 FROM 표결")
    }
    집계행들 = []
    for row in c.all_pages(표결집계_API, AGE=22):
        번호 = row["BILL_NO"]
        if not conn.execute("SELECT 1 FROM 의안 WHERE 의안번호=?", (번호,)).fetchone():
            continue
        값 = 표결집계행(row)
        with db.트랜잭션(conn):
            conn.execute(
                "INSERT INTO 표결집계 (의안번호,의결일,재적수,투표수,찬성수,반대수,기권수)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(의안번호,의결일) DO UPDATE SET"
                "  재적수=excluded.재적수, 투표수=excluded.투표수, 찬성수=excluded.찬성수,"
                "  반대수=excluded.반대수, 기권수=excluded.기권수",
                tuple(값.values()),
            )
        수치["집계"] += 1
        if 번호 not in 이미:
            # ⚠️ **응답의 BILL_ID 를 그대로 들고 간다.** DB 를 다시 뒤져 의안ID 를 쓰면 안 된다.
            집계행들.append((번호, row["BILL_ID"]))

    if 표본:
        집계행들 = 집계행들[:표본]
    진행 = net.진행자(로그, "표결 명단", len(집계행들), c)
    for i, (번호, 표결id) in enumerate(집계행들, 1):
        진행(i)
        rows, _ = c.json(표결명단_API, AGE=22, BILL_ID=표결id, pIndex=1, pSize=1000)
        if not rows:
            # ⚠️ **빈 응답을 성공으로 확정하지 마라.** 종전에는 `DELETE` 뒤에 아무것도 안
            #    넣고 `clear_failure` 까지 불러, 그 의안이 **재시도 큐에서 빠진 채** 명단만
            #    영영 비었다. A10 이 빨개지지만 왜인지는 어디에도 안 남는다.
            # ⚠️ 그리고 재의결로 한 의안에 의결일이 둘이 되면 같은 실행에서 두 번 도는데
            #    (`이미` 는 루프 전에 한 번 계산된다), 두 번째 빈손이 첫 패스의 명단을 지운다.
            수치["빈명단"] += 1
            # ⚠️ **원장에 남기기 전에 그것이 풀릴 길이 있는지 봐라.** 이 의안이 이미 행을
            #    가졌다면(위 재의결 경우) 다음 실행의 `이미` 가 걸러 **다시 요청되지 않는다**
            #    — 그런데 원장의 시도횟수는 안 늘고 안 지워져, 상한에 닿으면 A11 이 영영
            #    빨간데 사람이 할 수 있는 일이 없다. 이 파일이 처음부터 경고한 모양이다.
            #    (`db.재시도큐` 로 대상에 합류시키는 길이 8단계에서 생겼지만 여기서는
            #    안 쓴다 — 재의결로 한 의안이 같은 실행에서 두 번 도는 경우, 두 번째
            #    빈손이 매 실행 '재시도' 를 다시 적어 요청 하나짜리 무한 루프가 된다.
            #    아래 `continue` 는 그 짝을 **새로 만들지 않을 뿐**이다. 이 가드 이전에
            #    남은 (표결 행 + '재시도') 짝이 있으면 `이미` 에 걸려 영영 안 물어지고,
            #    시도횟수가 안 늘어 A11 도 안 울린다 — R6(수집실패 종류별)에만 보인다.
            #    실측 2026-08-28 그런 행은 0건이다.)
            if conn.execute(
                "SELECT 1 FROM 표결 WHERE 의안번호=?", (번호,)
            ).fetchone():
                continue
            db.record_failure(conn, "의안표결", 번호, "재시도", "표결 명단이 0행")
            continue
        좋은, 나쁜 = [], 0
        for r in rows:
            v = 표결행(r)
            if v and v[1] in 의원들:
                좋은.append(v)
            elif not v:
                나쁜 += 1
        with db.트랜잭션(conn):
            conn.execute("DELETE FROM 표결 WHERE 의안번호=?", (번호,))
            conn.executemany(
                "INSERT OR IGNORE INTO 표결 (의안번호,의원코드,표결결과) VALUES (?,?,?)", 좋은
            )
            if 나쁜:
                db.record_failure(conn, "의안표결", 번호, "막힘", f"CHECK 밖 표결결과 {나쁜}건")
                수치["막힘"] += 1
            else:
                db.clear_failure(conn, "의안표결", 번호)
        수치["명단"] += len(좋은)
    로그(
        f"표결집계 {수치['집계']:,}건 · 표결 {수치['명단']:,}행"
        f" (막힘 {수치['막힘']} · 빈 명단 {수치['빈명단']})"
    )
    return 수치


def collect_대안(conn, c: net.Client, 로그=print, 표본: int | None = None) -> dict:
    """9단계 — 위원장 대안에만 묻는다. **원안에 물으면 자기 자신만 온다.**

    완료 판정은 **그 대안을 가리키는 원안이 하나라도 있는가**다. 원장을 완료 표시로 쓰면
    "한 번도 안 물어봄"과 "성공함"이 같은 상태가 된다.
    """
    대상 = [
        r[0]
        for r in conn.execute(
            "SELECT b.의안번호 FROM 의안 b"
            " WHERE b.제안자구분='위원장' AND b.의안명 LIKE '%(대안)%'"
            "   AND NOT EXISTS (SELECT 1 FROM 의안 o WHERE o.대안의안번호 = b.의안번호)"
        )
    ]
    if 표본:
        대상 = 대상[:표본]
    수치 = {"대안": 0, "연결": 0, "실패": 0}
    진행 = net.진행자(로그, "대안", len(대상), c)
    for i, 번호 in enumerate(대상, 1):
        진행(i)
        의안id = conn.execute("SELECT 의안ID FROM 의안 WHERE 의안번호=?", (번호,)).fetchone()[0]
        try:
            html = c.post_text(
                f"{net.LIKMS}/bill/bi/bill/detail/anBillInfo.do",
                data={"billId": 의안id},
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Referer": f"{net.LIKMS}/bill/bi/billDetailPage.do?billId={의안id}",
                },
            )
            원안들 = parse_anbill(html, 번호)
        except (net.APIError, ValueError) as e:
            with db.트랜잭션(conn):
                db.record_failure(conn, "의안대안", 번호, "재시도", f"parse: {e}")
            수치["실패"] += 1
            continue
        with db.트랜잭션(conn):
            for 원안 in 원안들:
                # ⚠️ 자기 자신을 가리키면 안 된다(parse 가 이미 걸렀다). FK 는 없다 —
                #    대안이 원안보다 늦게 접수되는 것이 정상이라 전방 참조다.
                conn.execute(
                    "UPDATE 의안 SET 대안의안번호=? WHERE 의안번호=? AND 의안번호<>?",
                    (번호, 원안, 번호),
                )
            db.clear_failure(conn, "의안대안", 번호)
        수치["대안"] += 1
        수치["연결"] += len(원안들)
    로그(f"대안 {수치['대안']:,}건 → 원안 {수치['연결']:,}건 연결 (실패 {수치['실패']})")
    return 수치


def collect_요약(conn, c: net.Client, 로그=print, 표본: int | None = None) -> dict:
    """4단계 — 제안이유 및 주요내용. **여기가 최대 비용이다**(의안당 1회, 약 20,500요청).

    ⚠️ **`제안이유및주요내용 IS NULL` 을 '미수집'의 유일한 판정자로 쓰면 안 된다.** 제안이유가
    원래 비어 있는 의안 부류가 있다(결의안 220건 중 216건). 원장이 함께 판정한다.

    ⚠️ **조건에서 `수집시각` 을 빼라.** 2단계가 전 의안의 수집시각을 먼저 찍으므로 여기
    시점에는 "수집시각도 없는 의안"이 공집합이다.
    """
    대상 = [
        r[0]
        for r in conn.execute(
            "SELECT 의안번호 FROM 의안 b"
            " WHERE 제안이유및주요내용 IS NULL"
            "   AND NOT EXISTS (SELECT 1 FROM 수집실패 f"
            "                   WHERE f.대상종류='의안요약' AND f.대상키=b.의안번호"
            "                     AND f.실패종류='없음')"
            " ORDER BY 의안번호 DESC"
        )
    ]
    if 표본:
        대상 = 대상[:표본]
    수치 = {"요약": 0, "없음": 0}
    진행 = net.진행자(로그, "제안이유", len(대상), c)
    for i, 번호 in enumerate(대상, 1):
        진행(i)
        rows, _ = c.json(요약_API, BILL_NO=번호)
        본문 = (rows[0].get("SUMMARY") if rows else None) or None
        with db.트랜잭션(conn):
            if 본문:
                db.upsert_의안(
                    conn, "BPMBILLSUMMARY", {"의안번호": 번호, "제안이유및주요내용": 본문}
                )
                db.clear_failure(conn, "의안요약", 번호)
                수치["요약"] += 1
            else:
                db.record_failure(conn, "의안요약", 번호, "없음", "SUMMARY 가 비어 있다")
                수치["없음"] += 1
    로그(f"제안이유 {수치['요약']:,}건 (원천에 없음 {수치['없음']:,})")
    return 수치


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    ap.add_argument("--rate", type=float, default=8.0)
    ap.add_argument(
        "--sample", type=int,
        help="비싼 패스(제안이유·보완·표결명단·대안)를 이 건수만 받는다",
    )
    ap.add_argument(
        "--only", nargs="*",
        choices=("목록", "보완", "소위", "발의자", "표결", "대안", "요약"),
        help="일부 패스만 (기본: 전부)",
    )
    a = ap.parse_args()

    # ⚠️ **손으로 돌리는 경로도 락을 잡는다.** 종전에는 `collect.py` 만 잡아서, 예약
    #    수집이 도는 중에 이걸 돌리면 두 프로세스가 같은 DB 에 동시에 썼다.
    # ⚠️ **연결은 락 뒤에 연다** — `connect()` 가 파일을 만들고 WAL 설정을 파일에 남기므로,
    #    앞에 두면 물러날 실행도 DB 를 건드린 뒤에 물러난다.
    경로 = db.쓸_DB(a.db)
    잠금 = db.락(경로)
    with 잠금:
        if not 잠금.잡음:
            return 3
        conn = db.connect(경로)
        db.init_schema(conn)
        return _돌린다(conn, a)


def _돌린다(conn, a) -> int:
    할것 = a.only or ("목록", "보완", "소위", "발의자", "표결", "대안", "요약")
    try:
        with net.Client(rate=a.rate) as c:
            번호들: set[str] = set()
            if "목록" in 할것:
                번호들 = collect_목록(conn, c)["번호들"]  # type: ignore[assignment]
            else:
                번호들 = {r[0] for r in conn.execute("SELECT 의안번호 FROM 의안")}
            if "보완" in 할것:
                collect_보완(conn, c, 번호들, 표본=a.sample)
            if "소위" in 할것:
                collect_소위(conn, c)
            if "발의자" in 할것:
                collect_발의자(conn, c)
            if "표결" in 할것:
                collect_표결(conn, c, 표본=a.sample)
            if "대안" in 할것:
                collect_대안(conn, c, 표본=a.sample)
            if "요약" in 할것:
                collect_요약(conn, c, 표본=a.sample)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
