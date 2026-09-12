#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27", "selectolax>=0.3"]
# ///
"""회의 · 발언 · 회의의안.

**본문에서 읽는 것은 셋뿐이다 — `mnts_id`(오배송 가드) · `class_id`(회의종류 교차확인) · 발언.**
회기·차수·회의일자·위원회명·안건이 전부 열거 API 에 있다. 계획 초안이 본문 헤더에서 읽으려던
것을 옮긴 것인데, 그렇게 해서 함정 넷이 통째로 사라졌다 — 첫 `<h1>` 이 페이지 제목인 것,
`p.tit_sm` 이 본문에 8~47회 나오는 것, 국정감사 회기·차수 특례, 날짜 문자열 파싱.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from selectolax.parser import HTMLParser  # noqa: E402

from congress import db  # noqa: E402
from congress import net  # noqa: E402

열거_API = "ncwgseseafwbuheph"       # 위원회 회의록. DAE_NUM + CONF_DATE(부분문자열)
감사_API = "VCONFAPIGCONFLIST"       # 국정감사. ERACO='제22대'
본문URL = f"{net.RECORD}/assembly/viewer/minutes/minutes/xml.do"
의원상세 = "https://www.assembly.go.kr/members/22nd/{}"

# 본문 스크립트의 class_id ↔ 열거 API 의 CLASS_NAME. 둘이 일치해야 한다.
회의종류표 = {2: "상임위원회", 3: "특별위원회", 4: "예산결산특별위원회", 5: "국정감사", 6: "국정조사"}
담는종류 = ("상임위원회", "특별위원회", "예산결산특별위원회", "국정감사")

KST = timezone(timedelta(hours=9))
# ⚠️ **둘 다 달력이 아니라 임기라는 사실이다** — 그래서 파생하지 않고, 23대가 오면
#    `DAE_NUM=22` 와 함께 움직인다. 하한을 파생시키면 원천에 없는 연도를 매년 하나씩 더
#    묻게 되고, 상한을 안 두면 2029년부터 22대 회의가 있을 수 없는 연도를 계속 묻는다.
대수시작연도, 대수종료연도 = 2024, 2028      # 제22대 임기 2024-05-30 ~ 2028-05-29


def 올해() -> int:
    """⚠️ **KST 로 읽는다.** 러너는 UTC 라 UTC 로 읽으면 1월 1일 오전 아홉 시까지 새해가
    안 오고, 그 아홉 시간 동안 그 해 회의가 열거에서 통째로 빠진다."""
    return datetime.now(KST).year


def parse_enum(rows) -> tuple[list[dict], dict[int, set[str]]]:
    """`ncwgseseafwbuheph` 응답 → `(회의들, {회의id: {의안번호}})`.

    ⚠️ **행이 회의 단위가 아니라 (회의 × 안건) 단위다.** 2026년 9,410행이 실제로는 회의
    수백 건이다. `CONFER_NUM` 으로 접어야 회의 수가 나온다.

    ⚠️ **`TITLE` 에서 회기·차수만 집는다.** 위원회명은 `COMM_NAME` 이 정본이다 — 정규식이
    적게 집을수록 덜 깨진다.
    """
    회의: dict[int, dict] = {}
    안건: dict[int, set[str]] = {}
    for r in rows:
        번호 = int(r["CONFER_NUM"])
        if 번호 not in 회의:
            제목 = r.get("TITLE") or ""
            회기 = re.search(r"제(\d+)회", 제목)
            차수 = re.search(r"제(\d+)차", 제목)
            종류 = (r.get("CLASS_NAME") or "").strip()
            회의[번호] = {
                "회의id": 번호,
                "회의코드": r.get("CONF_ID"),
                "회의종류": 종류,
                "위원회명": (r.get("COMM_NAME") or "").strip(),
                "회기": int(회기.group(1)) if 회기 else None,
                "차수": int(차수.group(1)) if 차수 else None,
                "회의일자": (r.get("CONF_DATE") or "").strip(),
            }
        # ⚠️ 원천이 강조 표시로 `#` 를 끼워 넣는다: '…임명동의안`#`(의안번호 2219175)`#`'.
        #    번호 자체는 온전하므로 정규식이 그대로 통과한다.
        for 의안 in re.findall(r"의안번호\s*(\d{7})", r.get("SUB_NAME") or ""):
            안건.setdefault(번호, set()).add(의안)
    return list(회의.values()), 안건


def parse_감사(rows) -> list[dict]:
    """`VCONFAPIGCONFLIST` 응답 → 회의들. **안건은 없다**(원천이 안 주고, `VCONFBILLLIST` 도 `INFO-200`).

    ⚠️ **`DGR` 을 차수로 넣지 마라 — `'개회식'` 처럼 숫자가 아닌 값이 온다.** 국정감사의
    차수는 NULL 이 정상이다. 회기는 `SESS` 에 있다(초안의 "회기도 차수도 없다"는 본문 기준이었다).
    """
    회의 = []
    for r in rows:
        m = re.search(r"[?&]id=(\d+)", r.get("DOWN_URL") or "")
        if not m:
            continue
        회기 = re.search(r"제(\d+)회", r.get("SESS") or "")
        차수 = re.fullmatch(r"제?(\d+)차?", (r.get("DGR") or "").strip())
        회의.append(
            {
                "회의id": int(m.group(1)),
                "회의코드": r.get("CONF_ID"),
                "회의종류": "국정감사",
                "위원회명": (r.get("CMIT_NM") or "").strip(),
                "회기": int(회기.group(1)) if 회기 else None,
                "차수": int(차수.group(1)) if 차수 else None,
                "회의일자": (r.get("CONF_DT") or "").strip(),
            }
        )
    return 회의


def 사진키(url: str) -> str:
    """사진 URL → 비교용 키.

    ⚠️ **경로가 아니라 basename 으로 비교하라.** 회의록 img 경로가 3종이다 —
    `openassm/new/thumb/{해시}.png` · `openassm/new/{해시}.png` · `openassm/{의원코드}.jpg`.

    ⚠️ **확장자에 대문자가 섞인다**(`.JPG`). ⚠️ **파일명이 해시라고 가정하지 마라** —
    22대 320명 중 25명은 `{의원코드}.jpg` 형태다. `[0-9a-f]{32}` 를 쓰면 그 25명이 통째로 빠진다.
    """
    이름 = (url or "").rsplit("/", 1)[-1]
    return 이름.rsplit(".", 1)[0].lower()


def parse_minutes(html: str, 기대id: int) -> tuple[int, int | None, list[dict]]:
    """회의록 본문 → `(mnts_id, class_id, 발언들)`.

    ⚠️ **문서가 자기 id 를 밝히지 않으면 저장하지 마라.** '괜찮다'가 아니라 **'확인할 수
    없다'** 이고, 확인할 수 없는 것을 확인된 것처럼 저장하는 것이 여기서 가장 위험하다.

    ⚠️ **같은 URL 이 요청마다 다른 회의를 줄 수 있다**(원천의 캐시 계층 문제로 보인다).
    옛 프로젝트는 그 상태로 받은 2,047건 중 28건이 남의 본문이었다. **대수만 봐서는 못 막는다**
    — 틀린 문서도 22대일 수 있다.
    """
    m = re.search(r"const\s+mnts_id\s*=\s*(\d+)", html)
    if not m:
        raise ValueError(f"회의 {기대id}: 본문이 mnts_id 를 밝히지 않는다 — 확인할 수 없다")
    실제 = int(m.group(1))
    if 실제 != 기대id:
        raise ValueError(f"오배송: {기대id} 를 요청했는데 본문은 {실제} 다")
    c = re.search(r"const\s+class_id\s*=\s*(\d+)", html)
    class_id = int(c.group(1)) if c else None

    발언: list[dict] = []
    tree = HTMLParser(html)
    for i, 블록 in enumerate(tree.css("div[id^=spk_]"), start=1):
        속성 = 블록.attributes
        # ⚠️ **판별자는 data-mem_id 다.** class 의 spk_mem 은 비의원 블록도 단다(780개 전부).
        #    여기 속으면 비의원 278명을 의원으로 저장한다.
        mem_id = (속성.get("data-mem_id") or "").strip()
        문단 = [s.text(strip=True) for s in 블록.css("span.spk_sub")]
        img = 블록.css_first(".man img")
        a = 블록.css_first(".man a")
        href = (a.attributes.get("href") or "") if a else ""
        발언.append(
            {
                # ⚠️ `spk_N` 의 번호를 순서로 쓰지 마라 — 오름차순이지만 연속이 아니고
                #    전부 spk_2 에서 시작한다. 등장 순서로 1부터 다시 매긴다.
                "순서": i,
                "발언자명": 속성.get("data-name") or "",
                "직위": 속성.get("data-pos") or None,
                "의원인가": mem_id not in ("", "0"),
                "mem_id": mem_id,
                "사진키": 사진키(img.attributes.get("src") or "") if img else "",
                "slug": href.rstrip("/").rsplit("/", 1)[-1] if "/members/" in href else None,
                "내용": "\n".join(x for x in 문단 if x),
            }
        )
    return 실제, class_id, 발언


def 임시회의록인가(html: str) -> bool:
    """본문이 **아직 텍스트가 아닌** 임시회의록인가.

    ⚠️ **발언 0건을 하나로 뭉뚱그리지 마라 — 원인이 둘이고 처분이 반대다.** 원천이 아직
    텍스트를 안 만들었으면 다시 받아 봐야 같으므로 `'없음'` 이고, 표지가 없는데 0건이면
    **파서가 깨진 것**이라 `'막힘'` 으로 남겨 사람이 고칠 때까지 감사가 가리켜야 한다.
    둘 다 `'재시도'` 로 적으면 재시도 상한에 닿아 게이트가 영구히 빨갛다.

    실측 55355(제429회 국회운영위원회회의록 제99호 · 2025-09-06): `<p class="tmp">(임시회의록)</p>`
    가 있고 발언 블록 0개 · 18KB. 같은 날 표본으로 잰 정식 회의록 7건은 `p.tmp` 0건 ·
    발언 블록 3~1,356개 · 34KB~1.2MB 였다. 2025년 9월 회의가 11개월째 이 상태다.
    """
    tmp = HTMLParser(html).css_first("p.tmp")
    return bool(tmp) and "임시회의록" in tmp.text(strip=True)


def resolve_의원코드(발언: dict, 사진색인: dict[str, str], slug캐시: dict[str, str]) -> str | None:
    """발언자 → 의원코드. **3단 경로다. 1단만으로는 5명 중 1명이 안 이어진다.**

    ① 사진 파일명(basename) 대조 — 실패율 21%(실측 61명 중 13명). www 쪽 사진이 갱신되면
       회의록에 박힌 옛 파일명과 어긋난다. **"추가 요청 0회로 공짜"가 아니다.**
    ② `.man` 의 slug 로 의원 상세 페이지에서 코드 읽기 — `data-mem_id` 가 사람당 1:1 이라
       한 번 푼 것을 캐시하면 **비용이 발언당이 아니라 의원당 1회**다.
    ③ 그래도 실패하면 NULL + `수집실패('발언의원')`.

    ⚠️ **이름으로 추측해 채우지 마라.** 22대에 박지원이 둘이고, `data-name` 이 한자인 경우가
    있다(`柳榮夏`).
    """
    if not 발언.get("의원인가"):
        return None
    if 코드 := 사진색인.get(발언.get("사진키") or ""):
        return 코드
    if (slug := 발언.get("slug")) and (코드 := slug캐시.get(slug)):
        return 코드
    return None


def collect(conn, c: net.Client, 로그=print, 표본: int | None = None) -> dict:
    """10·11단계 — 열거 전량 → 회의별로 본문·발언·회의의안을 **한 트랜잭션**에.

    ⚠️ **열거에 날짜 창을 두지 않는다.** 창을 두면 ① 회의록이 수 주 뒤에 공개되므로
    "최근 것은 들어왔는데 두 달 전이 비어 있다"가 성공처럼 보이고, ② 열거 결과를 메모리에만
    들고 있으므로 **본문 전에 죽으면 그 회의가 `회의` 에도 `수집실패` 에도 없다.**
    전 연도 열거는 ~50요청이라 없는 비용이다.
    """
    # ── 열거 ──────────────────────────────────────────────────────────────────
    행들 = []
    # ⚠️ **상한을 상수로 박으면 그날이 와도 아무 소리가 안 난다.** `range(2024, 2027)` 은
    #    2027-01-01 부터 그 해 회의를 통째로 빼는데, 열거도 수집도 감사도 전부 초록이다 —
    #    회의록은 원래 수 주 뒤에 공개되므로 **"아직 안 올라왔나 보다"와 구별되지 않는다.**
    #    `max`/`min` 은 시계가 어긋난 러너에서 range 가 빈손이 되는 것을 막는다(빈손 열거는
    #    원천이 조용히 빈손을 주는 것과 같은 모양이라 그 자체로 사고다).
    for 연도 in range(대수시작연도, min(max(올해(), 대수시작연도), 대수종료연도) + 1):
        행들 += list(c.all_pages(열거_API, DAE_NUM=22, CONF_DATE=연도))
    회의들, 안건 = parse_enum(행들)
    회의들 += parse_감사(list(c.all_pages(감사_API, ERACO="제22대")))
    회의들 = [m for m in 회의들 if m["회의종류"] in 담는종류]
    로그(f"회의 열거 {len(회의들):,}건 · 안건이 있는 회의 {len(안건):,}건")

    # ── 사진 색인 (의원당 1회 비용의 다리) ────────────────────────────────────
    사진색인: dict[str, str] = {}
    for r in c.all_pages("ALLNAMEMBER"):
        if "제22대" in (r.get("GTELT_ERACO") or "") and r.get("NAAS_PIC"):
            사진색인[사진키(r["NAAS_PIC"])] = r["NAAS_CD"]
    의원들 = {r[0] for r in conn.execute("SELECT 의원코드 FROM 의원")}

    # ── 본문 ──────────────────────────────────────────────────────────────────
    # ⚠️ **할 일은 "아직 없는 것"이 아니라 "아직 없는 것 + 원장이 재시도라고 적어 둔 것"이다.**
    #    `회의` 표만 보면 한 번 성공한 뒤의 재수집 실패가 영영 안 잡힌다 — 데이터가 남아
    #    있으니 받은 것으로 보이는데 원장에는 실패가 있고, 다시 요청되지 않으니 시도횟수도
    #    안 늘어 **A11 조차 영영 안 울린다**(`db.재시도큐` 참고).
    이미 = {r[0] for r in conn.execute("SELECT 회의id FROM 회의")}
    큐 = db.재시도큐(conn, "회의본문")
    # ⚠️ **상한을 큐에서만 지키면 아래 둘째 목록이 그것을 도로 주워 온다.** 본문 실패는
    #    `회의` 행을 남기지 않고 빠져나가므로, 상한에 닿아 큐에서 빠진 항목이 곧바로
    #    "아직 안 받은 회의"가 된다 — 시도횟수가 끝없이 오르는 동안 A11 은 그것을 이미
    #    "포기한 항목"이라 부르고 있다(`db.포기한것` 참고).
    포기 = db.포기한것(conn, "회의본문")
    # ⚠️ **큐가 표본보다 앞이어야 한다.** `--sample N` 은 목록 앞에서 자르므로, 뒤에 두면
    #    표본 실행이 도는 동안 실패한 회의는 영영 순번이 안 온다.
    대상 = [m for m in 회의들 if str(m["회의id"]) in 큐]
    # ⚠️ **열거에 없다고 포기하지 마라.** 본문 URL 은 회의id 하나면 되고, 회기·차수·
    #    위원회명은 이미 `회의` 행에 있다 — 원천이 그 회의를 더 이상 열거하지 않아도
    #    다시 받을 근거가 전부 우리 안에 있다. 로그만 남기면 원장이 영영 안 비고
    #    시도횟수도 안 늘어 **A11 조차 안 울린다.**
    못푸는것 = []
    for 키 in sorted(큐 - {str(m["회의id"]) for m in 회의들}):
        열 = ("회의id", "회의코드", "회의종류", "위원회명", "회기", "차수", "회의일자")
        if 행 := conn.execute(
            f"SELECT {','.join(열)} FROM 회의 WHERE 회의id=?", (키,)
        ).fetchone():
            대상.append(dict(zip(열, 행)))
        else:
            못푸는것.append(키)
    대상 += [
        m for m in 회의들
        if str(m["회의id"]) not in 큐 and str(m["회의id"]) not in 포기
        and m["회의id"] not in 이미
    ]
    if 못푸는것:
        # 열거에도 `회의` 에도 없다 — 회의 행을 만들 재료가 없어 정말로 할 수 있는 일이
        # 없다. 원장 행은 남아 큐 건수가 안 줄므로, 말하지 않으면 아무도 모르는 정지다.
        로그(f"  ⚠️ 재시도 큐 {len(못푸는것)}건이 열거에도 `회의` 에도 없다:"
           f" {', '.join(못푸는것[:10])}")
    if 표본:
        대상 = 대상[:표본]
    slug캐시: dict[str, str] = {}
    수치 = {"회의": 0, "발언": 0, "미연결": 0, "실패": 0, "회의의안": 0}
    진행 = net.진행자(로그, "회의 본문", len(대상), c)

    for m in 대상:
        진행(수치["회의"] + 수치["실패"])
        회의id = m["회의id"]
        try:
            html = c.text(
                f"{net.RECORD}/assembly/viewer/minutes/xml.do",
                params={"id": 회의id, "type": "view"},
            )
            _, class_id, 발언들 = parse_minutes(html, 회의id)
        except net.원천에없음 as e:
            # 뷰어가 404 를 준다 = **그 회의의 전문을 원천이 갖고 있지 않다.** 재시도하지
            # 않는 것이 정상이고, A1 계열 게이트가 이걸 구멍에서 제외한다.
            with db.트랜잭션(conn):
                db.record_failure(conn, "회의본문", str(회의id), "없음", str(e)[:200])
            수치["실패"] += 1
            continue
        except (net.APIError, ValueError) as e:
            with db.트랜잭션(conn):
                db.record_failure(conn, "회의본문", str(회의id), "재시도", f"{type(e).__name__}: {e}")
            수치["실패"] += 1
            continue
        if not 발언들:
            # ⚠️ **0건의 원인이 둘이고 처분이 반대다.** 임시회의록은 원천이 아직 텍스트를
            #    안 만든 것이라 다시 받아 봐야 같고, 표지가 없는데 0건이면 파싱이 깨진 것이다.
            #    둘 다 '재시도' 로 적으면 상한에 닿아 A11 이 영구히 빨갛다(실측 55355).
            종류, 상세 = (
                ("없음", "임시회의록 — 원천에 발언 기록이 없다")
                if 임시회의록인가(html)
                else ("막힘", "발언 0건 — 파서를 확인해야 한다")
            )
            with db.트랜잭션(conn):
                db.record_failure(conn, "회의본문", str(회의id), 종류, 상세)
            수치["실패"] += 1
            continue
        # class_id 교차확인 — 열거 API 와 본문이 서로 다른 회의를 말하면 안 된다.
        if class_id and 회의종류표.get(class_id) and 회의종류표[class_id] != m["회의종류"]:
            with db.트랜잭션(conn):
                db.record_failure(
                    conn, "회의본문", str(회의id), "막힘",
                    f"회의종류 불일치: 열거 {m['회의종류']} vs 본문 class_id {class_id}",
                )
            수치["실패"] += 1
            continue

        # ⚠️ **큐로 다시 받는 회의는 발언이 이미 차 있다.** 여태 `회의` 표에 있는 회의는
        #    두 번 처리되지 않아 아래 `DELETE 발언` 이 언제나 0행 위에서 돌았는데, 재시도
        #    큐가 합류하면서 **차 있는 회의 위에서 돌게 됐다.** 본문이 반토막으로 와도
        #    파싱은 성공하므로(`if not 발언들` 은 0건만 막는다) 1,356개짜리 회의가 3개로
        #    조용히 교체된다 — 감사의 어느 등식도 발언 행수를 회의별로 재지 않는다.
        기존발언 = conn.execute(
            "SELECT COUNT(*) FROM 발언 WHERE 회의id=?", (회의id,)
        ).fetchone()[0]
        if 기존발언 and (막는것 := db.교체막는것(len(발언들), 기존발언)):
            # '막힘' 이다 — 다시 받아도 원천이 같은 반토막을 주면 같으므로 재시도가 아니고,
            # 원천이 정말 줄인 것이면 사람이 보고 판단할 일이다.
            # ⚠️ 작은 회의에서 10% 는 거칠다(3→2 는 33% 라 막힌다). **그래도 그쪽으로
            #    틀린다** — 오탐의 값은 A4 에 뜨는 '막힘' 한 건이고, 크기별로 느슨하게
            #    푼 자리의 값은 1,356행짜리 회의가 반토막으로 조용히 교체되는 것이다.
            #    (가드가 선 지금 조용히 지나가는 것은 10% 이하 감소뿐이다.)
            #    `db.교체기준` 은 레포의 손잡이 하나뿐이라
            #    여기서 따로 완화하면 그 하나가 둘이 된다.
            with db.트랜잭션(conn):
                db.record_failure(conn, "회의본문", str(회의id), "막힘",
                                  f"본문 발언이 줄었다: {막는것}")
            수치["실패"] += 1
            continue

        # slug 폴백 — 사진으로 못 푼 사람만, **사람당 정확히 1회**
        # ⚠️ **실패도 캐시해야 한다.** 성공만 캐시하면 못 푼 slug 를 그 사람의 발언마다 다시
        #    받으러 간다 — 한 회의에 같은 사람이 수십 번 발언하므로 요청이 폭발한다.
        #    실측: 이걸 안 해서 표본 10건이 10분을 넘겼다.
        for s in 발언들:
            slug = s["slug"]
            if not (s["의원인가"] and slug and s["사진키"] not in 사진색인):
                continue
            if slug in slug캐시:      # None(실패)도 '해 봤다'로 친다
                continue
            slug캐시[slug] = None
            try:
                페이지 = c.text(의원상세.format(slug))
                for 후보 in re.findall(r"\b([A-Z0-9]{8})\b", 페이지):
                    if 후보 in 의원들:
                        slug캐시[slug] = 후보
                        break
            except net.APIError:
                pass

        # ⚠️ **한 회의는 통째로 들어가거나 통째로 안 들어가야 한다.** 회의만 들어가고
        #    발언이 안 들어가면 다음 실행의 `이미` 집합이 그 회의를 건너뛴다 — 발언 0행인
        #    회의가 영구히 남는다. `with db.트랜잭션(conn):` 이 왜 이걸 못 막는지는 `db.트랜잭션` 에 있다.
        with db.트랜잭션(conn):
            위원회명 = db.upsert_위원회(conn, m["위원회명"])
            conn.execute(
                "INSERT INTO 회의 (회의id,회의코드,회의종류,위원회명,회기,차수,회의일자)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT(회의id) DO UPDATE SET"
                "  회의코드=excluded.회의코드, 회의종류=excluded.회의종류,"
                "  위원회명=excluded.위원회명, 회기=excluded.회기,"
                "  차수=excluded.차수, 회의일자=excluded.회의일자",
                (회의id, m["회의코드"], m["회의종류"], 위원회명, m["회기"], m["차수"], m["회의일자"]),
            )
            # ⚠️ **CASCADE 를 믿지 마라.** 회의는 UPSERT 라 DELETE 가 발동하지 않는다 —
            #    명시적으로 안 지우면 재수집이 전부 PK 충돌로 롤백된다.
            conn.execute("DELETE FROM 발언 WHERE 회의id=?", (회의id,))
            행 = []
            미연결 = 0
            for s in 발언들:
                코드 = resolve_의원코드(s, 사진색인, slug캐시)
                if s["의원인가"] and not 코드:
                    미연결 += 1
                행.append((회의id, s["순서"], s["발언자명"], s["직위"], 코드, s["내용"]))
            conn.executemany(
                "INSERT INTO 발언 (회의id,순서,발언자명,직위,의원코드,내용) VALUES (?,?,?,?,?,?)", 행
            )
            # ⚠️ **빈손 열거가 있던 회의의안을 지우게 두지 마라.** 안건은 열거에만 있고,
            #    큐로 다시 받는 회의는 `회의의안` 이 이미 차 있다. 그날 열거가 그 회의의
            #    안건을 안 주면 여기서 통째로 지워지는데, **회의의안 0건인 회의가 이미
            #    468건이라(R12) 줄어든 것이 눈에 띄지도 않는다.** 새 회의는 기존이 0이라
            #    영향이 없고, 안건이 온 날은 그대로 교체된다.
            #    반대편 비용은 안다 — 원천이 정말로 그 회의의 안건 연결을 **지운** 경우
            #    낡은 연결이 남고 R12(0건 회의)는 그것을 안 센다. 회의 안건은 이미 끝난
            #    회의의 기록이라 지워질 일이 거의 없고, 빈손 응답은 실제로 겪은 실패다.
            if 이번안건 := 안건.get(회의id, ()):
                conn.execute("DELETE FROM 회의의안 WHERE 회의id=?", (회의id,))
                conn.executemany(
                    "INSERT OR IGNORE INTO 회의의안 (회의id,의안번호) VALUES (?,?)",
                    [(회의id, b) for b in 이번안건],
                )
            if 미연결:
                # ⚠️ **'재시도' 가 아니다.** 원장 5건을 지금 다시 받아 3단 경로를 그대로
                #    태워도 미연결 수가 기록값과 같았다(11=11 · 2=2 · 1=1 · 1=1 · 7=7) —
                #    원천이 그 발언자를 식별할 정보를 주지 않는 것이라 재시도에 값이 없다.
                #    R11 이 계속 가리키므로 개선 여지가 사라지지는 않는다.
                db.record_failure(
                    conn, "발언의원", str(회의id), "없음", f"의원코드 미해결 {미연결}명"
                )
            else:
                db.clear_failure(conn, "발언의원", str(회의id))
            db.clear_failure(conn, "회의본문", str(회의id))
        수치["회의"] += 1
        수치["발언"] += len(발언들)
        수치["미연결"] += 미연결
        수치["회의의안"] += len(안건.get(회의id, ()))

    로그(
        f"회의 {수치['회의']:,}건 · 발언 {수치['발언']:,}행 · 회의의안 {수치['회의의안']:,}행"
        f" (본문 실패 {수치['실패']} · 의원 미연결 {수치['미연결']:,})"
    )
    return 수치


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    ap.add_argument("--rate", type=float, default=6.0)
    ap.add_argument("--sample", type=int, help="회의 본문을 이 건수만 받는다")
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
        try:
            with net.Client(rate=a.rate) as c:
                collect(conn, c, 표본=a.sample)
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
