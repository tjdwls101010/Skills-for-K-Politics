"""DB 에 없는 것을 API 로 직접 부른다.

⚠️ **DB 에 있는 자료의 커맨드는 여기 없다 — 일부러 없다.** `직결.py 판례 --검색` 같은 것이 생기면 완결적인 DB 를 두고 API 로 물어 **상위 N 건만 보고 답하게** 된다. 법령·판례·헌재결정례·행정심판례·법령해석례·행정규칙은 전부 SQL 로 조회한다.

⚠️ **여기 결과는 DB 자료와 같은 신뢰도가 아니다.** 스냅샷이라 재현되지 않고 파싱 검증도 안 거친다. 답에 쓸 때는 출처를 밝혀라.
"""

from __future__ import annotations


import argparse
import json

import httpx
import sys
from pathlib import Path

from .. import 원천  # noqa: E402
from 법제처 import 정규화  # noqa: E402
from .첨부 import 첨부텍스트
from .렌더러 import 렌더_연혁, 렌더_연혁본문, 렌더_별표검색, 렌더_행정규칙별표검색, 렌더_별표목록, _별표찾기, _첨부링크, 첨부필요, 렌더_별표본문

# 자료종류(한국어) → (엔드포인트, target, 고정 파라미터, 도움말)
#
# ⚠️ **여기 있는 것은 전부 실호출로 확인한 것이다.** 카탈로그에 있어도 JSON 을 안 주는
#    target 이 있다(`lsHistory` 는 HTML 만 준다). 연혁은 그래서 `eflaw&nw=1` 로 간다 —
#    같은 것을 주면서 JSON 이고, `nw` 의 의미는 `검증.py` V7 이 지킨다.
커맨드 = {
    "연혁":     ("검색", "eflaw",      {"nw": 1},  "법령의 개정 이력 (--법령)"),
    "연혁본문": ("본문", "eflaw",      {},         "그 시점의 법령 본문 (--법령일련번호 --시행일)"),
    "체계도":   ("본문", "lsStmd",     {},         "상·하위법 체계도 (--법령)"),
    "신구법":   ("본문", "oldAndNew",  {},         "개정 전후 조문 비교 (--법령일련번호)"),
    "삼단비교": ("검색", "thdCmp",     {},         "법–시행령–시행규칙 대조 (--검색)"),
    "위임법령": ("본문", "lsDelegated", {},        "이 법이 위임한 하위 법령 (--법령일련번호)"),
    "관련법령": ("검색", "lsRlt",      {},         "관련법령 그래프 (--법령)"),
    "자치법규": ("검색", "ordin",      {},         "조례·규칙 16만건 (--검색). DB 에 없다"),
    "별표":     ("검색", "licbyl",     {},         "법령 별표·서식 (--검색). DB 에 없다"),
    "행정규칙별표": ("검색", "admbyl",  {},        "행정규칙 별표·서식 (--검색). DB 에 없다"),
    "별표목록": ("본문", "law", {}, "법령·행정규칙 본문 API의 별표 목록 (--법령일련번호 또는 --행정규칙일련번호)"),
    "별표본문": ("본문", "law", {}, "별표 텍스트 (--법령일련번호 또는 --행정규칙일련번호, --별표)"),
    "조약":     ("검색", "trty",       {},         "조약 (--검색). DB 에 없다"),
}

# ⚠️ **`--범위 본문` 이 없으면 별표는 제목만 뒤진다.** 원천의 기본값이 제목 검색이라
#    본문에만 있는 말은 통째로 안 잡히고, **0건이 "그런 기준이 없다"로 읽힌다.**
#    실측(admbyl `평가범위`): 제목 16건 vs 본문 2,472건. licbyl `수수료`: 256 vs 8,899.
#    별표를 부르는 이유가 대개 "구체적 기준치가 어디 있나"라 기본값을 본문으로 둔다.
_범위코드 = {"본문": 3, "제목": 1}
_별표커맨드 = ("별표", "행정규칙별표")


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="자료종류:\n" + "\n".join(f"  {k:9} {v[3]}" for k, v in 커맨드.items()),
    )
    ap.add_argument("자료종류", choices=list(커맨드), help="조회할 자료종류")
    ap.add_argument("--법령", help="법령명. 별표 검색에서는 받은 페이지 안의 사후 필터")
    ap.add_argument("--검색", help="검색어")
    ap.add_argument("--법령일련번호", help="MST")
    ap.add_argument("--시행일", help="2026-09-11 또는 20260911. 연혁본문에 필요하다 —"
                                    " 같은 MST 가 여러 시행일을 갖는다")
    ap.add_argument("--건수", type=int, help="검색 건수 (연혁 기본 100, 나머지 20)")
    ap.add_argument("--조", help="연혁본문에서 읽을 조 (예: 28의2)")
    ap.add_argument("--부칙", action="store_true", help="연혁본문의 부칙 전문")
    ap.add_argument("--별표", help="별표본문에서 읽을 번호 (예: 1의5)")
    ap.add_argument("--행정규칙일련번호", help="별표목록·별표본문의 행정규칙 ID")
    ap.add_argument("--첨부", action="store_true", help="별표본문을 인라인 텍스트 대신 첨부에서 추출")
    ap.add_argument("--원본", action="store_true", help="렌더링 대신 원본 JSON을 자르지 않고 한 줄로 출력")
    ap.add_argument("--저장", type=Path, help="원본 JSON을 파일에도 저장")
    ap.add_argument("--범위", choices=list(_범위코드), default="본문",
                    # ⚠️ argparse 가 help 를 `%` 포매팅에 통과시킨다. 리터럴 퍼센트는
                    #    `%%` 로 써야 하고, 안 그러면 `--help` 가 통째로 죽는다.
                    help=f"별표를 어디서 찾나. {'·'.join(_별표커맨드)} 에만 쓴다."
                         " 기본은 본문 — 제목만 뒤지면 실측으로 1%%도 안 걸린다"
                         " (admbyl '평가범위' 제목 16 vs 본문 2,472)")
    a = ap.parse_args(argv)

    엔드, target, 기본, _ = 커맨드[a.자료종류]
    q = {"target": target, **기본}
    if a.자료종류 in _별표커맨드:
        q["search"] = _범위코드[a.범위]
    if a.법령일련번호:
        q["MST"] = a.법령일련번호
    if a.자료종류 in ("별표목록", "별표본문"):
        if bool(a.법령일련번호) == bool(a.행정규칙일련번호):
            ap.error("--법령일련번호 또는 --행정규칙일련번호 하나가 필요하다")
        if a.행정규칙일련번호:
            q.update(target="admrul", ID=a.행정규칙일련번호)
        if a.자료종류 == "별표본문" and not a.별표:
            ap.error("별표본문은 --별표 번호가 필요하다")
    if a.시행일:
        # ⚠️ **원천은 아직 `YYYYMMDD` 만 받는다.** DB 는 `YYYY-MM-DD` 로 저장하므로,
        #    조회 결과를 그대로 복사해 넣는 손이 자연스럽다. 그때 원천은 에러가 아니라
        #    **0건**을 주고, 0건은 "그런 버전이 없다"로 읽힌다. 경계에서 되돌린다.
        시행일 = 정규화.날짜원천(a.시행일)
        if not 시행일:
            ap.error(f"--시행일 을 날짜로 못 읽었다: {a.시행일!r}")
        q["efYd"] = 시행일
    if a.법령 and a.자료종류 not in _별표커맨드:
        q["LM" if 엔드 == "본문" else "query"] = a.법령
    if a.검색:
        q["query"] = a.검색
    if 엔드 == "검색":
        q["display"] = a.건수 if a.건수 is not None else (100 if a.자료종류 == "연혁" else 20)

    if not (a.법령 or a.검색 or a.법령일련번호 or a.행정규칙일련번호):
        ap.error("--법령 · --검색 · --법령일련번호 중 하나는 있어야 한다")

    with 원천.Client(rate=8) as c:
        payload = c._json(원천.검색 if 엔드 == "검색" else 원천.본문, q)
    if "result" in payload and "msg" in payload:
        print(f"🔴 인증 실패: {payload['result']}", file=sys.stderr)
        return 1
    원본 = json.dumps(payload, ensure_ascii=False)
    if a.저장:
        a.저장.write_text(원본 + "\n", encoding="utf-8")
    if a.자료종류 == "연혁":
        봉투 = payload.get("LawSearch", {})
        받은수 = len(원천.행들(봉투.get("law")))
        if int(봉투.get("totalCnt") or 0) > 받은수:
            print(f"더 있다: 전체 {봉투['totalCnt']}건 중 {받은수}건. --건수로 늘릴 수 있다", file=sys.stderr)
    if a.원본:
        print(원본)
        return 0
    if a.자료종류 in _별표커맨드 and a.법령:
        봉투키, 행키, 이름키 = (("licBylSearch", "licbyl", "관련법령명") if a.자료종류 == "별표"
                             else ("admRulBylSearch", "admrulbyl", "관련행정규칙명"))
        봉투 = payload.get(봉투키, {})
        payload = {**payload, 봉투키: {**봉투, 행키: [r for r in 원천.행들(봉투.get(행키))
                                                         if a.법령 in r.get(이름키, "")]}}
    try:
        if a.자료종류 == "연혁본문":
            글 = 렌더_연혁본문(payload, 조=a.조, 부칙=a.부칙)
        elif a.자료종류 == "별표본문":
            try:
                if a.첨부:
                    raise 첨부필요(_첨부링크(_별표찾기(payload, a.별표)))
                글 = 렌더_별표본문(payload, a.별표)
            except 첨부필요 as e:
                if not e.링크:
                    raise ValueError("별표 텍스트와 첨부 링크가 모두 없다") from e
                try:
                    r = httpx.get("https://www.law.go.kr" + e.링크,
                                  headers={"User-Agent": 원천.UA}, follow_redirects=True, timeout=60)
                    r.raise_for_status()
                    글 = 첨부텍스트(r.content)
                except Exception as 오류:
                    print(f"🔴 첨부 추출 실패: {e.링크} · {오류}", file=sys.stderr)
                    return 1
        else:
            렌더러 = {"연혁": 렌더_연혁, "별표": 렌더_별표검색,
                    "행정규칙별표": 렌더_행정규칙별표검색, "별표목록": 렌더_별표목록}
            글 = 렌더러[a.자료종류](payload) if a.자료종류 in 렌더러 else 원본
    except (LookupError, ValueError) as e:
        print(f"🔴 {e}", file=sys.stderr)
        return 1
    print(글)
    return 0
