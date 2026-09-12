#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""국회 원천에 요청하는 유일한 통로 — UA · 유량 · 재시도 · 응답 봉투 해석.

**이 도메인의 실패는 예외가 아니라 200 OK 에 빈 결과다.** 국회 API 는 데이터가 없을 때도,
키가 틀렸을 때도, 파라미터 이름이 틀렸을 때도 200 을 준다. 그 구별을 `unwrap()` 한 곳에
모아 둔 것이 이 파일의 요점이다.
"""

from __future__ import annotations

import os
import sys
import random
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

HERE = Path(__file__).resolve().parent

# ⚠️ **UA 가 없으면 400 이다.** open.assembly.go.kr 과 record.assembly.go.kr 둘 다,
#    인증 오류도 JSON 도 아닌 `Bad Request.` 열두 바이트를 준다. 이 400 은 "API 가 죽었다"
#    처럼 보이므로, UA 를 안 넣은 채 살아 있는 API 를 죽었다고 판정하기 쉽다.
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
OPENAPI = "https://open.assembly.go.kr/portal/openapi"
RECORD = "https://record.assembly.go.kr"
LIKMS = "https://likms.assembly.go.kr"

# 원천이 INFO-336 으로 거부하는 상한. 넘겨서 배우면 그 요청이 낭비다.
MAX_PAGE = 1000


class APIError(RuntimeError):
    """원천이 200 과 함께 돌려준 오류 코드. `INFO-200`(데이터 없음)은 여기 오지 않는다."""


class 원천에없음(APIError):
    """원천이 **그 자료를 갖고 있지 않다**고 답한 것(404). 일시적 오류가 아니다.

    ⚠️ **404 만 이 예외로 올려라.** `400` 은 UA·인증 오류이기도 하므로 재시도해야 한다.
    `수집실패` 에 `'없음'` 으로 남기면 감사가 그걸 구멍에서 제외한다.
    """


def load_key(env_path: Path | None = None) -> str:
    """인증키. 환경변수가 `.env` 를 이긴다 — 자동화가 키를 주입하는 유일한 수단이다.

    ⚠️ **키 없이 보내면 200 에 `INFO-300` 이 온다.** 요청을 다 돌고 나서 알게 되므로
    여기서 먼저 터뜨린다.
    """
    if key := os.environ.get("CONGRESS_API_KEY"):
        return key
    path = env_path if env_path is not None else HERE / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CONGRESS_API_KEY=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip("\"'")
    raise RuntimeError(
        f"CONGRESS_API_KEY 가 없다. 환경변수로 주거나 {path} 에 넣어라."
    )


def unwrap(payload: dict, api: str) -> tuple[list[dict], int]:
    """OpenAPI 응답 봉투에서 행과 총건수를 뽑는다.

    봉투가 두 모양이다.
        {"<API>": [{"head": [{"list_total_count": N}, {"RESULT": {...}}]}, {"row": [...]}]}
        {"RESULT": {"CODE": "INFO-200", "MESSAGE": "해당하는 데이터가 없습니다."}}

    ⚠️ **`INFO-200` 은 실패가 아니다.** 아직 존재하지 않는 의안번호가 정상적으로 그렇게
    답한다. 실패 원장에 넣으면 유령 재시도가 쌓인다.

    ⚠️ **그 밖의 코드는 반드시 예외로 올려라.** 빈 결과로 삼키면 **인증이 끊긴 날 조용히
    0건을 수집하고 초록불로 끝난다.**
    """
    if (result := payload.get("RESULT")) and len(payload) == 1:
        code = result.get("CODE", "")
        if code.startswith("INFO-200"):
            return [], 0
        raise APIError(f"{api}: {code} {result.get('MESSAGE', '')}")

    # ⚠️ 최상위 키를 `api` 로 하드코딩해 찾지 마라 — 대소문자나 별칭이 어긋나면 그 API
    #    하나가 조용히 0건이 된다. RESULT 가 아닌 유일한 키를 집는다.
    본문 = next((v for k, v in payload.items() if k != "RESULT"), None)
    if not isinstance(본문, list):
        raise APIError(f"{api}: 모르는 응답 모양 {list(payload)}")

    total, rows = 0, []
    for 조각 in 본문:
        if "head" in 조각:
            for h in 조각["head"]:
                if "list_total_count" in h:
                    total = int(h["list_total_count"])
                if (r := h.get("RESULT")) and not str(r.get("CODE", "")).startswith(
                    ("INFO-000", "INFO-200")
                ):
                    raise APIError(f"{api}: {r.get('CODE')} {r.get('MESSAGE', '')}")
        elif "row" in 조각:
            rows = 조각["row"]
    return rows, total


def page_count(total: int, size: int = MAX_PAGE) -> int:
    if size > MAX_PAGE:
        raise ValueError(f"페이지 크기 상한은 {MAX_PAGE} 이다 (원천이 INFO-336 으로 거부한다)")
    return -(-total // size) if total else 0


class Pacer:
    """손잡이는 하나다 — 초당 요청 수. 동시성은 여기서 자동으로 정해진다.

    둘을 따로 두면 상호작용을 사람이 계산하게 된다. 요청제한은 명세상 '제한없음'이고
    실측에서도 안 걸렸지만, 상대는 공공 서비스다.
    """

    def __init__(self, rate: float = 8.0):
        self.간격 = 1.0 / rate if rate > 0 else 0.0
        self._다음 = 0.0

    def wait(self) -> None:
        if self.간격:
            if (남은 := self._다음 - time.monotonic()) > 0:
                time.sleep(남은)
            self._다음 = time.monotonic() + self.간격


진행간격 = 15.0


def 진행자(로그, 라벨: str, 전체: int | None = None, c: "Client | None" = None,
          간격: float | None = None, 시계=time.monotonic):
    """`진행(현재)` 를 돌려준다. **마지막 출력 후 `간격` 초가 지났을 때만** 한 줄 찍는다.

    ⚠️ **건수 기준("N건마다")으로 쓰지 마라.** 한 건이 느린 패스에서 몇 분씩 조용하고,
    그러면 Actions 로그만 보고는 **러너가 죽은 것과 도는 것을 구별할 수 없다.**
    그리고 러너가 죽으면 `upload-artifact` 가 403 으로 실패해 파일 로그가 통째로
    사라지므로(실측 2026-08-19), 남는 것은 stdout 으로 흘린 것뿐이다.
    """
    간격 = 진행간격 if 간격 is None else 간격
    시작 = 마지막 = 시계()

    def 진행(현재: int) -> None:
        nonlocal 마지막
        지금 = 시계()
        if 지금 - 마지막 < 간격:
            return
        마지막 = 지금
        몫 = f"{현재:,}/{전체:,}" if 전체 else f"{현재:,}"
        조각 = [f"[{라벨}] {몫}", f"{지금 - 시작:,.0f}초"]
        if c is not None:
            조각 += [f"요청 {c.요청수:,}", f"백오프 {c.backoff_횟수:,}"]
        로그("  " + " · ".join(조각))

    return 진행


class Client:
    """UA·유량·재시도를 한 곳에 둔다. 이걸 거치지 않는 요청 경로를 만들지 마라."""

    def __init__(self, rate: float = 8.0, timeout: float = 60.0, retries: int = 5, 로그=None):
        self.pacer = Pacer(rate)
        # 목록 전량 조회가 스스로 진행을 알린다. **가장 긴 침묵이 여기다** — 실측으로
        # 판례 목록 하나가 2분 18초를 조용히 돌았다. 기본은 None(조용)이라
        # 라이브러리 호출과 테스트가 stdout 을 더럽히지 않는다.
        self.로그 = 로그
        self.retries = retries
        self.backoff_횟수 = 0
        # 진행 표시가 읽는다. **건수가 안 움직이는 구간에서 살아 있다는 유일한 증거다** —
        # 회의 본문의 slug 폴백처럼 한 건 안에서 요청을 수십 번 쓰는 자리가 있다.
        self.요청수 = 0
        self._c = httpx.Client(
            headers={"User-Agent": UA},
            timeout=timeout,
            follow_redirects=True,
            http2=True,
        )

    def close(self) -> None:
        self._c.close()

    def __enter__(self) -> Client:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _보낸다(self, method: str, url: str, **kw) -> httpx.Response:
        마지막 = None
        for 시도 in range(self.retries):
            self.pacer.wait()
            self.요청수 += 1
            try:
                r = self._c.request(method, url, **kw)
                if 400 <= r.status_code < 600 and r.status_code != 404:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code}", request=r.request, response=r
                    )
                # ⚠️ 404 만 '없음'이다. 나머지 4xx 는 UA·인증 오류일 수 있어 재시도한다.
                if r.status_code == 404:
                    raise 원천에없음(f"{url}: HTTP {r.status_code}")
                r.raise_for_status()
                return r
            except 원천에없음:
                raise
            except (httpx.HTTPError, httpx.StreamError) as e:
                마지막 = e
                if 시도 == self.retries - 1:
                    break
                self.backoff_횟수 += 1
                # 지수 백오프 + 지터. 지터가 없으면 여러 실패가 같은 순간에 몰려 재개된다.
                time.sleep(min(30.0, 2**시도) * (0.5 + random.random()))
        raise APIError(f"{url}: {type(마지막).__name__}: {마지막}") from 마지막

    def json(self, api: str, **params) -> tuple[list[dict], int]:
        """OpenAPI 한 페이지. `(행, 총건수)` 를 준다."""
        params.setdefault("Key", load_key())
        params.setdefault("Type", "json")
        r = self._보낸다("GET", f"{OPENAPI}/{api}", params=params)
        try:
            payload = r.json()
        except ValueError as e:
            # UA 가 빠졌을 때 오는 `Bad Request.` 12바이트가 여기로 온다.
            raise APIError(f"{api}: JSON 이 아니다 — {r.text[:80]!r}") from e
        return unwrap(payload, api)

    def all_pages(self, api: str, size: int = MAX_PAGE, **params):
        """전량 열거. 1페이지의 `list_total_count` 로 페이지 수를 정한다.

        ⚠️ **행이 빌 때까지 도는 방식을 쓰지 마라.** 중간 페이지가 일시적으로 빈손이면
        거기서 멈추고 **성공으로 끝난다.** 총건수를 믿고 정확히 그만큼 돈다.

        ⚠️ **총건수와 실수신이 어긋나면 예외다 — 작은 목록이 아니라 실패한 요청이다.**
        조용히 적은 행을 돌려주면 원천이 절반만 준 날 그 절반이 정상적인 전량이 되고,
        "이 목록이 원천의 현재 전부다"에 기대는 교체 가드(의원위원회·현직·표결)가
        **바로 그 잘못된 전제 위에서 초록으로 통과한다.** 여기서 거부하면 목록을 쓰는
        모든 경로가 옵트인 없이 함께 보호된다.
        """
        rows, total = self.json(api, pIndex=1, pSize=size, **params)
        # 이 메서드는 최소 스탠드인 self 로도 불린다(테스트·다른 호출 경로).
        로그 = getattr(self, "로그", None)
        진행 = 진행자(로그, f"목록 {api}", total, self) if 로그 else None
        받음 = len(rows)
        yield from rows
        for p in range(2, page_count(total, size) + 1):
            더, _ = self.json(api, pIndex=p, pSize=size, **params)
            받음 += len(더)
            if 진행:
                진행(받음)
            yield from 더
        if 받음 != total:
            raise APIError(
                f"{api}: 총건수 {total:,} 인데 {받음:,} 행만 왔다 — 불완전한 열거다"
            )

    def text(self, url: str, **kw) -> str:
        return self._보낸다("GET", url, **kw).text

    def post_text(self, url: str, data: dict, headers: dict | None = None) -> str:
        return self._보낸다("POST", url, data=data, headers=headers or {}).text
