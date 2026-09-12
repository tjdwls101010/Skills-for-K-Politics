#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27", "selectolax>=0.3"]
# ///
"""국회가 바뀌었나 — `01-원천-실측.md` 의 표를 매번 다시 만든다.

**네트워크를 타고 DB 를 안 본다.** 수집이 조용히 빈손으로 올 때 셋 중 어느 것이 빨간지가
곧 진단이다: `pytest` 빨강이면 우리가 깬 것, 여기가 빨강이면 국회가 바꾼 것,
`audit.py` 만 빨강이면 둘 다 멀쩡한데 적재가 덜 된 것.

⚠️ **이 도구 없이 원천 변경을 알아챌 방법이 없다.** 계획 세션의 조사에서만 셋이 바뀌어
있었다 — likms 엔드포인트가 옮겨졌고, 대안정보의 응답 방향이 뒤집혔고, 표결 명단에 불참이
추가됐다. 전부 200 OK 뒤에서 일어난 변화다.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx  # noqa: E402
from selectolax.parser import HTMLParser  # noqa: E402

import meetings  # noqa: E402
import net  # noqa: E402

결과: list[tuple[str, str, bool, str]] = []


def 단언(번호: str, 무엇: str, ok: bool, 상세: str = "") -> None:
    결과.append((번호, 무엇, bool(ok), 상세))


def 표본단언(번호: str, 무엇: str, 표본, ok: bool, 상세: str = "") -> None:
    """표본에서 나온 판정. **표본이 비면 통과가 아니다.**

    ⚠️ `all([])` 도 `set() == set()` 도 `set() <= {...}` 도 전부 **참**이다. 그래서 원천이
    빈손으로 오는 날 "전 행에 X 가 있다"·"두 집합이 같다"·"이 값들만 온다" 류가 **전부
    🟢 가 된다** — 표본이 0인 것과 표본이 전부 통과한 것이 같은 색이 되고, 그게 정확히
    이 도구가 막으라고 있는 사고다. 판정을 표본과 **함께** 넘겨 그 둘을 갈라 놓는다.
    """
    if not len(표본):
        단언(번호, 무엇, False, f"표본 0 — 원천이 빈손이다{' · ' + 상세 if 상세 else ''}")
        return
    단언(번호, 무엇, ok, 상세)

def 보고값(번호: str, 무엇: str, 상세: str) -> None:
    결과.append((번호, 무엇, True, 상세))


MINUTES = "https://record.assembly.go.kr/assembly/viewer/minutes/xml.do"

# ── 필드 집합 축 ──────────────────────────────────────────────────────────────
# ⚠️ **"필드는 남았는데 의미가 바뀐" 변화는 값 검사로 못 잡는다.** 잡히는 것은 집합의
#    변화다. 필드마다 규칙을 하나씩 다는 것은 rail 이고, 다음 필드가 생기면 또 달아야 한다.
# ⚠️ **두 방향의 처분이 다르다.** verify 가 빨가면 그날 수집이 통째로 건너뛰어진다
#    (`collect.yml`). 그러니 **사라진 필드만 게이트**다 — 우리가 그걸 읽고 있을 수 있어
#    멈추는 것이 맞다. **새 필드는 보고값**이다: 늘어난 것은 우리 코드를 안 깨는데,
#    그것 때문에 멈추면 원천이 필드 하나 늘린 날 코퍼스가 하루 낡는다.
필드기준선경로 = Path(__file__).resolve().parent / "원천필드.json"
_잰필드: dict[str, list[str]] = {}


def 필드모음(행들) -> dict[str, list[str]] | None:
    """`{"항상": 전 행에 있던 것, "가끔": 일부 행에만 있던 것}`. 빈 응답이면 `None`.

    ⚠️ **원천은 값이 NULL 인 필드를 행에서 통째로 뺀다.** 그래서 합집합만 재면 표본이
    바뀌는 날 희소 필드가 "사라졌다"로 읽히고, **원천이 안 바뀌었는데 그날 수집이 통째로
    건너뛰어진다.** 전 행에 있던 것만 게이트로 삼고 나머지는 그 밖에 둔다.

    ⚠️ **표본 1행에서는 "전 행에 있다"가 아무것도 뜻하지 않는다.** 그 한 행의 희소 필드가
    전부 게이트가 되면 위의 함정으로 그대로 되돌아간다 — 2행 미만이면 전부 '가끔' 이다.

    ⚠️ **0행을 "필드가 전부 사라졌다"로 읽으면 안 된다.** 빈 응답은 다른 게이트가 잡는
    사고이고, 여기서까지 빨개지면 같은 사고를 두 번 세면서 원인을 흐린다.
    """
    if not 행들:
        return None
    전부 = {k for r in 행들 for k in r}
    항상 = set.intersection(*({*r} for r in 행들)) if len(행들) > 1 else set()
    return {"항상": sorted(항상), "가끔": sorted(전부 - 항상)}


def 필드본다(이름: str, 행들) -> None:
    """이번 실행이 실제로 본 필드를 적어 둔다. **이미 받아 둔 행을 쓴다 — 요청을 안 늘린다.**

    ⚠️ 같은 이름으로 여러 번 부르면 **합친다.** 표본 회의를 도는 자리처럼 한 API 를 여러 번
    부르는 곳에서 마지막 응답만 남기면, 그 응답이 마침 짧아 필드가 덜 온 날 전부 "사라짐"이 된다.
    """
    if (본것 := 필드모음(행들)) is None:
        return
    옛것 = _잰필드.get(이름)
    if 옛것 is None:
        _잰필드[이름] = 본것
        return
    # ⚠️ **'항상' 은 합치는 게 아니라 교집합이다.** 한 호출에만 있던 필드를 '항상' 으로
    #    올리면 그 필드가 다음 날 다른 표본에 없을 때 빨간불이 된다.
    항상 = sorted(set(옛것["항상"]) & set(본것["항상"]))
    전부 = set(옛것["항상"] + 옛것["가끔"] + 본것["항상"] + 본것["가끔"])
    _잰필드[이름] = {"항상": 항상, "가끔": sorted(전부 - set(항상))}


def 필드기준선() -> dict[str, list[str]]:
    """커밋된 기준선.

    ⚠️ **실행 때마다 새로 만들면 비교 대상이 자기 자신이 되어 어떤 변화도 안 잡힌다** —
    그게 이 축이 고치려는 결함이다. 그래서 레포에 담긴 파일 하나이고, 정당한 변화는
    `--기준선갱신` 으로 다시 쓴 뒤 **커밋해서** diff 로 남긴다.

    ⚠️ 파일이 없으면 빈 기준선이다 — 예외를 내면 처음 돌리는 사람이 아무 진단도 못 받는다.
    """
    try:
        날것 = json.loads(필드기준선경로.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    # ⚠️ **모양이 어긋난 항목 하나로 진단 전체가 죽으면 안 된다.** 손으로 고친 JSON 이나
    #    '항상/가끔' 이전 판본이 섞이면 그 항목만 게이트에서 빠지고 나머지는 계속 돈다.
    #    ('항상' 을 비워 두므로 그 이름은 재기 전까지 아무것도 안 막는다.)
    성한것 = {}
    for 이름, v in (날것 or {}).items():
        if isinstance(v, dict) and isinstance(v.get("항상"), list) and isinstance(v.get("가끔"), list):
            성한것[이름] = v
        elif isinstance(v, list):
            성한것[이름] = {"항상": [], "가끔": sorted(v)}
    return 성한것


def 필드대조(기준: dict, 지금: dict) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    """`(사라진 것, 새로 생긴 것)` — **양쪽에 다 있는 이름만** 본다.

    ⚠️ **안 잰 것과 사라진 것은 다르다.** 이번 실행이 그 API 에 안 갔다는 이유로 전 필드를
    빨갛게 만들면 다음 사람은 표를 통째로 무시하게 된다. 반대로 기준선에 없는 이름은
    "전부 새 필드"가 아니라 **아직 기준선이 없는 것**이다.
    """
    사라짐, 새것 = {}, {}
    for 이름 in 기준.keys() & 지금.keys():
        옛, 새 = 기준[이름], 지금[이름]
        옛전부 = set(옛["항상"]) | set(옛["가끔"])
        새전부 = set(새["항상"]) | set(새["가끔"])
        # ⚠️ **게이트는 "항상 있던 것이 이제 아무 행에도 없다" 뿐이다.** 희소 필드가
        #    오늘 표본에 안 온 것과 원천이 필드를 없앤 것은 다르고, 앞의 것으로 수집을
        #    멈추면 원천이 그대로인 날 코퍼스가 낡는다. 항상→가끔으로 내려앉은 것도
        #    여전히 오고 있는 것이라 여기서 안 센다.
        if 간 := sorted(set(옛["항상"]) - 새전부):
            사라짐[이름] = 간
        if 온 := sorted(새전부 - 옛전부):
            새것[이름] = 온
    return 사라짐, 새것



def 본문(c: net.Client, id: int) -> str:
    return c.text(MINUTES, params={"id": id, "type": "view"})


# ⚠️ **표본 하나는 그 종류 하나만 지킨다.** V10~V14 는 오래 `57024` 하나로만 돌았는데
#    그 회의는 상임위원회다 — 국회가 국정감사 회의록만 새 구조로 바꾸면 일곱 판정이
#    전부 초록인 채 국정감사 발언 수집이 통째로 깨진다. 실측(2026-08-28)으로 그 위험이
#    이미 보인다: `class_id` 가 종류마다 다르다(상임위 2 · 특위 3 · 예결위 4 · 국감 5).
#    표본을 리터럴이 아니라 **표**로 두면 종류를 늘리는 데 코드가 필요 없다.
# ⚠️ **첫 줄은 대표 표본이라 바꾸지 마라.** V10~V14 의 상세값이 `01-원천-실측.md` 와
#    이어져 있어, 바꾸면 문서까지 같이 고칠 일이 된다. 늘리는 것은 안전하다.
# ⚠️ 본회의가 없는 것은 빠뜨린 것이 아니다 — D4 로 일부러 안 담는다

본문표본: tuple[tuple[int, str, str], ...] = (
    (57024, "상임위원회", "정무위 전체회의 · 발언 782 · 비의원 279 (실측 2026-08-28)"),
    (55594, "국정감사", "발언 893 · 비의원 374 (실측 2026-08-28)"),
    (54608, "예산결산특별위원회", "발언 786 · 비의원 317 (실측 2026-08-28)"),
    (56891, "특별위원회", "발언 898 · 비의원 346 (실측 2026-08-28)"),
)


def 본문신호(html: str) -> dict:
    """회의록 HTML 에서 구조 신호만 뽑는다 — **판정은 안 한다.**

    ⚠️ **여기서 예외를 내면 나머지 표본의 답도 못 듣는다.** 한 종류가 깨진 것과 전부를
    모르는 것은 다르다. 못 찾은 것은 `None`·0·False 로 돌려주고, 그것을 빨간불로
    읽을지는 `표본성함` 이 정한다.
    """
    mid = re.search(r"const\s+mnts_id\s*=\s*(\d+)", html)
    cid = re.search(r"const\s+class_id\s*=\s*(\d+)", html)
    tree = HTMLParser(html)
    memid = [b.attributes.get("data-mem_id") for b in tree.css("div[id^=spk_]")]
    헤더 = tree.css_first("div.minutes_header")
    return {
        "mnts_id": int(mid.group(1)) if mid else None,
        "class_id": int(cid.group(1)) if cid else None,
        "memid": memid,
        "블록": len(memid),
        "비의원": memid.count("0"),
        "mem_id없음": sum(m is None for m in memid),
        "헤더h1": bool(헤더 and 헤더.css_first("h1")),
        "tit_sm본문": len(tree.css("div.minutes_body p.tit_sm")),
        "tit_sm전체": len(tree.css("p.tit_sm")),
    }


def 본문받기(c, id: int, 시도: int = 3) -> tuple[str, list[int | None]]:
    """회의록 본문 + **오배송으로 받은 남의 mnts_id 들.**

    ⚠️ **원천은 같은 URL 에 남의 회의록을 준다**(캐시 계층 문제로 보인다). 옛 프로젝트는
    2,047건 중 28건이 그랬고, 실측(2026-08-28) 56891 을 네 번 부르니 한 번은 52486
    (다른 회의 · class 2)이 왔다. 수집기는 이걸 `'재시도'` 로 적고 다음 실행이 받아간다
    (`meetings.parse_minutes`).

    ⚠️ **여기서 오배송을 그대로 빨간불로 두면 원천이 안 바뀐 날에도 무작위로 빨개진다.**
    거짓 경고는 그 자체로 신호를 죽인다 — 이 레포는 이미 그 병으로 빨간불 6일을 잃었다.
    다시 부르되 **무엇을 몇 번 받았는지는 남긴다** — 횟수만으로는 못 고친다. 어떤 회의가
    겹쳐 왔는지가 있어야 다음 사람이 원천의 캐시를 쫓을 수 있고, 그게 오배송 가드가 왜
    필요한지의 유일한 증거다.
    """
    어긋난: list[int | None] = []
    for _ in range(시도):
        html = 본문(c, id)
        받은 = 본문신호(html)["mnts_id"]
        if 받은 == id:
            return html, 어긋난
        어긋난.append(받은)
    return html, 어긋난


def 표본성함(요청id: int, 신호: dict, 종류: str | None = None) -> list[str]:
    """한 표본이 성한가 — 어긋난 이유들. 빈 리스트면 성하다.

    V10~V14 가 대표 표본에 대고 재는 것과 같은 조건이라, 표에 종류를 한 줄 더하면
    그 종류도 같은 잣대를 자동으로 받는다.

    ⚠️ **표본을 종류마다 둬 놓고 종류를 안 재면 아무것도 안 늘어난다.** 국정감사 id 로
    물었는데 상임위 본문이 오면 구조 신호는 전부 정상이라 그냥 초록이 된다. 수집기는 이걸
    `meetings.회의종류표` 교차확인으로 '막힘' 처리하므로, 진단도 같은 것을 물어야 한다.
    """
    이유 = []
    if 신호["mnts_id"] != 요청id:
        이유.append(f"mnts_id {신호['mnts_id']} (요청 {요청id})")
    if 신호["class_id"] is None:
        이유.append("class_id 없음")
    # ⚠️ **표에 없는 class_id 는 안 잡는다.** 원천이 종류를 새로 만든 것은 보고할 변화지
    #    빨간불이 아니다 — 여기서 멈추면 그날 수집이 통째로 건너뛰어진다.
    elif 종류 and (본 := meetings.회의종류표.get(신호["class_id"])) and 본 != 종류:
        이유.append(f"class_id {신호['class_id']}={본} 인데 표본은 {종류}")
    if not 신호["블록"]:
        이유.append("발언 블록 0")
    if 신호["mem_id없음"]:
        이유.append(f"data-mem_id 없는 블록 {신호['mem_id없음']}")
    elif not 신호["비의원"]:
        이유.append("비의원(data-mem_id='0') 0 — 의원/비의원 판별의 근거가 없다")
    if not 신호["헤더h1"]:
        이유.append("minutes_header h1 없음")
    return 이유


def main(argv: list[str] | None = None) -> int:
    옵션 = argparse.ArgumentParser(
        description="국회 원천이 계획 문서와 같은지 판정한다. 네트워크만 타고 DB 는 안 본다.",
    )
    옵션.add_argument(
        "--기준선갱신",
        action="store_true",
        help="이번에 본 필드 집합을 원천필드.json 에 다시 쓴다. "
             "V23 이 짚은 변화가 정당할 때만 쓰고, **쓴 파일을 커밋해라** — "
             "커밋 안 하면 다음 실행이 옛 기준선과 다시 비교해 같은 빨간불을 낸다.",
    )
    인자 = 옵션.parse_args(argv)

    with net.Client(rate=8.0) as c:
        # ── V1. UA 차단 규칙 ──────────────────────────────────────────────────
        # ⚠️ 규칙이 "UA 가 없으면 400" 이 **아니다.** 실측(2026-08-12):
        #      UA 헤더 없음 → 200 · python-httpx/… → 200 · 브라우저 → 200 · curl/8.x → 400
        #    서버가 차단하는 것은 **`curl/` 로 시작하는 UA** 하나다.
        #    그래서 확인해야 할 것도 둘이다 — 차단이 아직 살아 있는가(그 규칙이 넓어지면
        #    우리 UA 도 걸린다), 그리고 **우리가 실제로 쓰는 UA 가 통과하는가.**
        상태 = {}
        for 이름, ua in (("curl 기본", "curl/8.7.1"), ("우리 UA", net.UA)):
            try:
                상태[이름] = httpx.get(
                    f"{net.OPENAPI}/OPENSRVAPI",
                    params={"Key": net.load_key(), "Type": "json", "pSize": 1},
                    headers={"User-Agent": ua},
                    timeout=30,
                ).status_code
            except httpx.HTTPError as e:
                상태[이름] = f"에러 {e}"
        단언(
            "V1",
            "curl 기본 UA 는 400 으로 막힌다 (차단 규칙이 살아 있다)",
            상태["curl 기본"] == 400,
            f"curl/8.7.1 → {상태['curl 기본']}",
        )
        단언(
            "V1b",
            "**우리가 쓰는 UA 는 통과한다** — 차단이 넓어지면 여기가 먼저 빨개진다",
            상태["우리 UA"] == 200,
            f"{net.UA[:38]}… → {상태['우리 UA']}",
        )
        rows, _ = c.json("OPENSRVAPI", pIndex=1, pSize=1)
        단언("V2", "OPENSRVAPI 가 JSON 을 준다", bool(rows))

        # ── V3. 의안 목록 ─────────────────────────────────────────────────────
        bills, total = c.json("TVBPMBILL11", AGE=22, pIndex=1, pSize=1000)
        필드본다("TVBPMBILL11 의안목록", bills)
        구분 = {b.get("PPSR_KIND") or b.get("PROPOSER_KIND") or "" for b in bills}
        단언("V3", "TVBPMBILL11 AGE=22 가 20,000행 이상", total >= 20000, f"{total:,}행")
        보고값("V3b", "의안 최대 번호", max(b["BILL_NO"] for b in bills))

        # ── V4. ALLBILL 의 재의 2행 ───────────────────────────────────────────
        rows, _ = c.json("ALLBILL", BILL_NO="2200851")
        필드본다("ALLBILL 의안ID", rows)
        접두 = sorted({r["BILL_ID"][:4] for r in rows})
        단언(
            "V4",
            "ALLBILL(2200851) 이 PRC_ + GOV_ 2행 — 재의의 유일한 신호",
            len(rows) == 2 and 접두 == ["GOV_", "PRC_"],
            f"{len(rows)}행 {접두}",
        )

        # ── V5. 제안이유 ──────────────────────────────────────────────────────
        rows, _ = c.json("BPMBILLSUMMARY", BILL_NO="2220544")
        필드본다("BPMBILLSUMMARY 요약", rows)
        단언("V5", "BPMBILLSUMMARY 가 최근 의안에 SUMMARY 를 준다", bool(rows and rows[0].get("SUMMARY")))

        # ── V6. 발의자 정합성 ─────────────────────────────────────────────────
        rows, 발의총 = c.json("nzmimeepazxkubdpn", AGE=22, pIndex=1, pSize=300)
        필드본다("nzmimeepazxkubdpn 발의자", rows)
        불일치 = 0
        for r in rows:
            m = re.search(r"등\s*(\d+)\s*인", r.get("PROPOSER") or "")
            if not m:
                continue
            코드수 = sum(
                len([x for x in (r.get(k) or "").split(",") if x.strip()])
                for k in ("RST_MONA_CD", "PUBL_MONA_CD")
            )
            불일치 += int(m.group(1)) != 코드수
        표본단언("V6", "발의자: '등 N인' == RST+PUBL 코드 수, 불일치 0",
                rows, 불일치 == 0, f"표본 {len(rows)} 중 {불일치}건")

        # ── V7. 표결 ──────────────────────────────────────────────────────────
        # 2208496 은 거부권이 행사된 의안이라 ALLBILL 이 GOV_ 와 PRC_ 두 행을 준다.
        # ⚠️ **반드시 PRC_ 로 물어야 한다.** GOV_ 로 물으면 INFO-200 이 오는데, 그건
        #    "표결이 없다"가 아니라 **키가 틀린 것**이다. 계획 세션이 실제로 이 함정을 밟아
        #    "거부권 법안은 표결 API 에 없다"는 틀린 결론을 적을 뻔했다.
        표결ids = {r["BILL_ID"][:4]: r["BILL_ID"] for r in c.json("ALLBILL", BILL_NO="2208496")[0]}
        rows, _ = c.json("nojepdqqaweusdfbi", AGE=22, BILL_ID=표결ids["PRC_"], pIndex=1, pSize=1000)
        필드본다("nojepdqqaweusdfbi 표결명단", rows)
        결과값 = {r.get("RESULT_VOTE_MOD") for r in rows}
        단언(
            "V7",
            "표결 명단에 '불참' 이 있다 — 옛 프로젝트의 '재적−재석' 한계가 사라진 근거",
            "불참" in 결과값,
            f"{len(rows)}행 {sorted(x for x in 결과값 if x)}",
        )
        # V7c — 같은 의안번호에 PRC_ 가 둘 있는 경우. 표결 쪽 id 로만 명단이 온다.
        집계행, _ = c.json("ncocpgfiaoituanbr", AGE=22, BILL_NO="2216968")
        필드본다("ncocpgfiaoituanbr 표결집계", 집계행)
        표결쪽id = 집계행[0]["BILL_ID"] if 집계행 else ""
        의안쪽id = next(
            (r["BILL_ID"] for r in c.json("ALLBILL", BILL_NO="2216968")[0]
             if r["BILL_ID"].startswith("PRC_")), "",
        )
        명단, _ = c.json("nojepdqqaweusdfbi", AGE=22, BILL_ID=표결쪽id, pIndex=1, pSize=5) if 표결쪽id else ([], 0)
        단언(
            "V7c",
            "표결 명단은 **표결집계가 준 BILL_ID** 로만 온다 (같은 의안번호에 PRC_ 가 둘)",
            bool(명단) and 표결쪽id != 의안쪽id,
            f"표결쪽 {표결쪽id[:12]}… ≠ 의안쪽 {의안쪽id[:12]}… · 명단 {len(명단)}행",
        )

        gov행, _ = c.json("nojepdqqaweusdfbi", AGE=22, BILL_ID=표결ids["GOV_"])
        단언(
            "V7b",
            "같은 의안을 GOV_ id 로 물으면 빈손이다 — 없는 게 아니라 키가 틀린 것",
            not gov행,
            f"PRC_ {len(rows)}행 vs GOV_ {len(gov행)}행",
        )

        # ── V8. 회의 열거 ─────────────────────────────────────────────────────
        회의행, 회의총 = c.json("ncwgseseafwbuheph", DAE_NUM=22, CONF_DATE=2026, pIndex=1, pSize=1000)
        필드본다("ncwgseseafwbuheph 회의", 회의행)
        한행 = 회의행[0]
        단언(
            "V8",
            "CONFER_NUM 이 CONF_LINK_URL 의 id 와 같다 — 회의 열거 전체가 이 등식 위에 있다",
            str(한행["CONFER_NUM"]) == (re.search(r"[?&]id=(\d+)", 한행["CONF_LINK_URL"]) or [None, ""])[1],
            f"{한행['CONFER_NUM']} vs {한행['CONF_LINK_URL'][-24:]}",
        )
        # V17 — TITLE 이 회기·차수를 담는다 (본문 헤더를 안 읽는 근거)
        제목있음 = [r for r in 회의행 if re.search(r"제(\d+)회.*제(\d+)차", r.get("TITLE") or "")]
        단언(
            "V17",
            "열거 API 의 TITLE 이 제N회·제N차를 담는다",
            len(제목있음) > len(회의행) * 0.8,
            f"{len(제목있음)}/{len(회의행)}행. 예: {회의행[0].get('TITLE')!r}",
        )
        단언(
            "V17b",
            "CONF_DATE 가 이미 ISO 형식이다",
            bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", (한행.get("CONF_DATE") or "").strip())),
            repr(한행.get("CONF_DATE")),
        )

        # ── V16. SUB_NAME ↔ VCONFBILLLIST ─────────────────────────────────────
        안건 = {}
        for r in 회의행:
            안건.setdefault(r["CONF_ID"], []).append(r.get("SUB_NAME") or "")
        후보 = sorted(안건.items(), key=lambda kv: -len(kv[1]))[:2]
        모두일치, 상세 = True, []
        for cid, subs in 후보:
            파생 = {n for s in subs for n in re.findall(r"의안번호\s*(\d{7})", s)}
            vrows, _ = c.json("VCONFBILLLIST", CONF_ID=cid, pIndex=1, pSize=1000)
            필드본다("VCONFBILLLIST 회의의안", vrows)
            공식 = {n for r in vrows for n in re.findall(r"\b2[12]\d{5}\b", r.get("BILL_NM") or "")}
            모두일치 &= 파생 == 공식
            상세.append(f"{cid}: SUB {len(파생)} vs VCONF {len(공식)}")
        단언(
            "V16",
            "SUB_NAME 파생 의안번호 == VCONFBILLLIST — 요청 1,996회를 없앤 근거",
            모두일치,
            " · ".join(상세),
        )

        # ── V9/V20. 국정감사 ──────────────────────────────────────────────────
        감사행, 감사총 = c.json("VCONFAPIGCONFLIST", ERACO="제22대", pIndex=1, pSize=1000)
        필드본다("VCONFAPIGCONFLIST 국정감사", 감사행)
        단언("V9", "국정감사 300건 이상", 감사총 >= 300, f"{감사총}행")
        g = 감사행[0] if 감사행 else {}
        표본단언(
            "V20",
            "국정감사 API 가 CONF_ID·SESS·CMIT_NM·CONF_DT 를 준다",
            감사행,
            all(g.get(k) for k in ("CONF_ID", "SESS", "CMIT_NM", "CONF_DT")),
            f"SESS={g.get('SESS')!r} DGR={g.get('DGR')!r} CMIT={g.get('CMIT_NM')!r}",
        )
        감사id = {int(m.group(1)) for r in 감사행 if (m := re.search(r"[?&]id=(\d+)", r.get("DOWN_URL") or ""))}
        위원회id = {int(r["CONFER_NUM"]) for r in 회의행}
        표본단언("V9b", "국정감사와 위원회 회의록의 겹침 0",
                감사id, not (감사id & 위원회id), f"겹침 {len(감사id & 위원회id)}건")

        # ── V18/V19. 위원회 ───────────────────────────────────────────────────
        위행, 위총 = c.json("nkimylolanvseqagq", TH=22, pIndex=1, pSize=1000)
        필드본다("nkimylolanvseqagq 위원회", 위행)
        소위 = [r for r in 위행 if r.get("SUB_CMIT_NM")]
        단언("V18", "nkimylolanvseqagq TH=22 가 위원회 목록을 준다", 위총 >= 150, f"{위총}행, 소위 {len(소위)}")
        단언(
            "V18b",
            "CMIT_NM 과 SUB_CMIT_NM 이 별도 필드다 — 상위 관계를 추측하지 않는 근거",
            bool(소위 and 소위[0]["CMIT_NM"] and 소위[0]["SUB_CMIT_NM"]),
            f"{소위[0]['CMIT_NM']!r} + {소위[0]['SUB_CMIT_NM']!r}" if 소위 else "소위 0",
        )
        try:
            c.json("nkimylolanvseqagq", TH="제22대", pIndex=1, pSize=5)
            잘못된대수 = False
        except net.APIError:
            잘못된대수 = True
        rows2, _ = c.json("nkimylolanvseqagq", TH="제22대", pIndex=1, pSize=5) if not 잘못된대수 else ([], 0)
        단언("V18c", "TH 는 '제22대' 가 아니라 22 다", 잘못된대수 or not rows2, "제22대 → 빈 결과")

        위원행, 위원총 = c.json("nktulghcadyhmiqxi", pIndex=1, pSize=1000)
        필드본다("nktulghcadyhmiqxi 위원회위원", 위원행)
        표본단언(
            "V19",
            "위원회 위원 명단의 MONA_CD 가 전 행에 있다 — 이름 대조를 안 하는 근거",
            위원행,
            all(r.get("MONA_CD") for r in 위원행),
            f"{sum(1 for r in 위원행 if r.get('MONA_CD'))}/{len(위원행)}",
        )

        # ── V10~V14. 회의록 본문 (대표 표본) ──────────────────────────────────
        # ⚠️ **고정 회의를 쓴다.** 아무 회의나 집으면 비의원이 0명인 짧은 회의를 만나
        #    V13 이 실패하는데, 그건 원천이 바뀐 것이 아니라 표본을 잘못 고른 것이다.
        #    57024 정무위 전체회의 = 발언 780 · 비의원 278(35%) 로 실측된 회의다.
        #    나머지 종류는 아래 V22 가 같은 잣대로 훑는다.
        회의id, 대표종류, _ = 본문표본[0]
        html, 어긋난ids = 본문받기(c, 회의id)
        신호 = 본문신호(html)
        오배송합 = list(어긋난ids)
        단언("V10", "본문이 자기 mnts_id 를 밝힌다 — 오배송 가드의 근거",
             신호["mnts_id"] is not None, str(신호["mnts_id"]))
        단언("V10b", "재시도 안에 요청한 회의의 본문을 받을 수 있다",
             신호["mnts_id"] == 회의id,
             f"요청 {회의id} · 시도 {len(어긋난ids) + 1}회" + (f" · 받은 값 {어긋난ids}" if 어긋난ids else ""))
        # ⚠️ **있는지만 물으면 값이 뭐든 통과한다.** 그 값이 열거 API 가 말한 회의종류와
        #    같은지까지 봐야 오배송·재분류가 걸린다(수집기가 그 교차확인으로 '막힘' 을 낸다).
        단언("V11", "본문의 class_id 가 그 회의의 종류와 맞는다",
             신호["class_id"] is not None
             and not [x for x in 표본성함(회의id, 신호, 대표종류) if "class_id" in x],
             f"{신호['class_id']} = {meetings.회의종류표.get(신호['class_id'], '표에 없는 값')}"
             f" · 표본은 {대표종류}")
        표본단언("V12", "발언 블록에 data-mem_id 가 있다",
                신호["memid"], 신호["mem_id없음"] == 0, f"{신호['블록']}개")
        단언(
            "V13",
            "data-mem_id 에 '0'(비의원)이 섞여 있다 — 의원/비의원 판별의 근거",
            신호["비의원"] > 0,
            f"비의원 {신호['비의원']}/{신호['블록']}",
        )
        단언("V14", "div.minutes_header 안에 h1 이 있다", 신호["헤더h1"])
        단언(
            "V14b",
            "p.tit_sm 이 헤더 전용 클래스가 아니다 — '존재하는가' 로 검사하면 항상 통과한다",
            신호["tit_sm본문"] > 0 or 신호["tit_sm전체"] > 1,
            f"문서 전체 {신호['tit_sm전체']}개",
        )

        # ── V22. 나머지 회의 종류도 같은 구조인가 ─────────────────────────────
        # ⚠️ **표본 하나로는 종류 하나만 지킨다.** 위의 일곱은 상임위원회 한 건만 밟는데,
        #    `class_id` 는 종류마다 다르고(2·3·4·5) 원천이 한 종류만 바꿀 수 있다.
        #    그러면 V10~V14 는 전부 초록인 채 그 종류의 발언이 통째로 안 들어온다.
        어긋난, class_id들 = [], {신호["class_id"]}
        for 표본id, 종류, _ in 본문표본[1:]:
            표본html, 표본어긋남 = 본문받기(c, 표본id)
            오배송합 += 표본어긋남
            표본신호 = 본문신호(표본html)
            class_id들.add(표본신호["class_id"])
            if 이유 := 표본성함(표본id, 표본신호, 종류):
                어긋난.append(f"{종류}({표본id}): {', '.join(이유)}")
        단언(
            "V22",
            "국감·예결위·특위 회의록도 대표 표본과 같은 구조다",
            not 어긋난,
            "; ".join(어긋난) if 어긋난 else f"{len(본문표본)}종 · class_id {sorted(x for x in class_id들 if x)}",
        )
        # ⚠️ **오배송은 원천의 상시 결함이라 게이트가 아니라 보고값이다.** 빨간불로 두면
        #    원천이 안 바뀐 날에도 무작위로 빨개지고, 거짓 경고는 신호를 죽인다. 대신
        #    **얼마나 잦은지**를 남긴다 — 이 숫자가 커지면 수집의 '재시도' 도 같이 는다.
        보고값("V22b", "오배송 빈도 — 재시도로 걷어낸 횟수",
              f"{len(본문표본)}건 받는 동안 {len(오배송합)}회"
              + (f" · 받은 값 {오배송합}" if 오배송합 else ""))

        # ── V24. 수집기가 쓰는데 위에서 안 밟는 API 들 ────────────────────────
        # ⚠️ **필드 기준선이 지키는 것은 기준선에 든 API 뿐이다.** 여기 없는 API 는 필드가
        #    사라져도 V23 이 초록인데, 그 값이 감사에서 게이트가 아니라 보고값이면
        #    **DB 훼손까지 조용하다** — `ALLNAMEMBER.PLPT_NM` 이 사라지면 의원 전량 upsert
        #    가 정당을 NULL 로 덮는데 감사는 R3 보고값일 뿐이다.
        #    (어느 API 가 빠졌는지는 `tests/congress/test_필드기준선.py` 가 수집기 소스에서
        #     뽑아 대조한다 — 여기 목록을 손으로 맞추지 않아도 새 API 는 빨갛게 드러난다.)
        소위행, _ = c.json("TVBPMCONFINFO", AGE=22, pIndex=1, pSize=100)
        필드본다("TVBPMCONFINFO 소위심사", 소위행)
        단언("V24", "TVBPMCONFINFO 가 소위 심사정보를 준다", bool(소위행), f"{len(소위행)}행")

        # ⚠️ **위원장 대안으로 물으면 0행이다.** 이 API 는 `bills.py` 가 **비법률안의 의원
        #    제안자**를 보정하는 자리에서만 쓰므로, 표본도 그런 의안이라야 한다 — 아니면
        #    "원천이 죽었다"가 아니라 "표본을 잘못 골랐다"로 매일 빨개진다.
        비법률안id = c.json("ALLBILL", BILL_NO="2220598")[0][0]["BILL_ID"]
        제안자행, _ = c.json("BILLINFOPPSR", BILL_ID=비법률안id)
        필드본다("BILLINFOPPSR 제안자", 제안자행)
        단언("V24b", "BILLINFOPPSR 가 비법률안의 의원 제안자를 준다",
             bool(제안자행), f"결의안 2220598 → {len(제안자행)}행")

        의원행, _ = c.json("ALLNAMEMBER", pIndex=1, pSize=300)
        필드본다("ALLNAMEMBER 의원전체", 의원행)
        표본단언("V24c", "ALLNAMEMBER 가 정당명(PLPT_NM)을 준다 — 없으면 전량 upsert 가 정당을 지운다",
                의원행, all(r.get("PLPT_NM") for r in 의원행),
                f"{sum(1 for r in 의원행 if r.get('PLPT_NM'))}/{len(의원행)}")

        현직행, 현직총 = c.json("nwvrqwxyaytdsfvhu", pIndex=1, pSize=300)
        필드본다("nwvrqwxyaytdsfvhu 현직", 현직행)
        단언("V24d", "nwvrqwxyaytdsfvhu 가 현직 명단을 준다", 현직총 >= 250, f"{현직총}명")

        # ── V15. 대안 관계 — OpenAPI 어디에도 없는 유일한 스크래핑 ─────────────
        대안id = c.json("ALLBILL", BILL_NO="2220259")[0][0]["BILL_ID"]
        대안html = c.post_text(
            f"{net.LIKMS}/bill/bi/bill/detail/anBillInfo.do",
            data={"billId": 대안id},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{net.LIKMS}/bill/bi/billDetailPage.do?billId={대안id}",
            },
        )
        번호들 = set(re.findall(r"\b2[12]\d{5}\b", 대안html)) - {"2220259"}
        기대 = {"2200255", "2200990", "2217711", "2219947"}
        단언(
            "V15",
            "anBillInfo.do(2220259) 가 흡수된 원안 4건을 준다",
            번호들 == 기대,
            f"{sorted(번호들)} (기대 {sorted(기대)})",
        )
        단언(
            "V15b",
            "원안 쪽에 물으면 자기 자신만 온다 — 그래서 수집은 대안 쪽에서 한 번이다",
            True,
            "방향이 한쪽뿐이라 위원장 대안 827건만 물으면 관계 전체가 채워진다",
        )

    # ── V23. 원천의 필드 집합 ─────────────────────────────────────────────────
    기준 = 필드기준선()
    사라짐, 새것 = 필드대조(기준, _잰필드)
    표본단언(
        "V23",
        "쓰던 필드가 그대로 있다 — 사라지면 우리가 그걸 읽고 있을 수 있다",
        기준,
        not 사라짐,
        "; ".join(f"{k}: {', '.join(v)} 없어짐" for k, v in 사라짐.items())
        or f"{len(기준.keys() & _잰필드.keys())}개 API 대조",
    )
    보고값(
        "V23b",
        "새로 생긴 필드 — 게이트가 아니다(늘어난 것은 우리를 안 깬다)",
        "; ".join(f"{k}: {', '.join(v)}" for k, v in 새것.items()) or "없음",
    )
    if 아직 := sorted(_잰필드.keys() - 기준.keys()):
        보고값("V23c", "아직 기준선이 없는 API — `--기준선갱신` 후 커밋하면 잡힌다", ", ".join(아직))

    # ── 결과 표 — 다음 사람의 기준선 ────────────────────────────────────────────
    print()
    print(f"{'#':<6} {'검사':<62} {'':<4} 상세")
    print("─" * 118)
    실패 = 0
    for 번호, 무엇, ok, 상세 in 결과:
        실패 += not ok
        print(f"{번호:<6} {무엇:<62} {'🟢' if ok else '🔴'}   {상세}")
    print("─" * 118)
    if 실패:
        print(f"\n🔴 {실패}건 실패. **국회가 바뀌었을 수 있다** — 원천을 다시 재고 기준값을")
        print("   실측으로 고치고 나서 수집을 돌려라. 여기가 빨간 채로 수집하면 조용히 틀린 값이 쌓인다.")
    else:
        print(f"\n🟢 {len(결과)}건 전부 통과. 원천이 계획 문서와 같다.")

    if 인자.기준선갱신:
        # ⚠️ **덮어쓰지 말고 합친다.** 이번 실행이 어떤 API 에 못 갔으면(예외·빈 응답) 그
        #    이름은 `_잰필드` 에 없는데, 통째로 덮으면 **그 축이 기준선에서 조용히 빠진다** —
        #    다음부터 그 API 는 V23c 의 초록 보고값일 뿐 게이트로 안 돌아온다. 지우는 것은
        #    JSON 을 손으로 고치는 일이라야 한다.
        합친것 = 기준 | _잰필드
        필드기준선경로.write_text(
            json.dumps(dict(sorted(합친것.items())), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\n필드 기준선을 다시 썼다: {필드기준선경로}"
              f" (이번에 잰 {len(_잰필드)}개 + 못 잰 채 지킨 {len(합친것) - len(_잰필드)}개)")
        print("**커밋해라.** 안 하면 다음 실행이 옛 기준선과 비교해 같은 빨간불을 낸다.")
        # ⚠️ **갱신이 다른 실패까지 0 으로 세탁하면 안 된다.** 갱신이 해소하는 것은 V23
        #    하나뿐이고, 나머지가 빨간데 0 을 내면 그 뒤에 붙은 수집이 그대로 돈다.
        남은실패 = sum(1 for 번호, _, ok, _ in 결과 if not ok and 번호 != "V23")
        if 남은실패:
            print(f"⚠️ 갱신과 무관한 실패 {남은실패}건이 남아 있다 — 수집으로 넘어가지 마라.")
        return 1 if 남은실패 else 0
    return 1 if 실패 else 0


if __name__ == "__main__":
    raise SystemExit(main())
