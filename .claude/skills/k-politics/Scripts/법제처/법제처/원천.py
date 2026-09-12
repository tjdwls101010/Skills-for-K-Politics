"""법제처 원천에 요청하는 유일한 통로 — 유량 · 동시성 · 재시도 · 응답 봉투 해석.

**이 도메인의 실패는 예외가 아니라 200 OK 에 다른 봉투다.** 인증이 틀렸을 때도, 자료가 없을 때도, `type=JSON` 을 빠뜨렸을 때도 200 이 온다. 그 구별을 `분류()` 한 곳에 모아 둔 것이 이 파일의 요점이다.

**target 마다 봉투 이름이 다르다.** `봉투` 표가 그래서 하드코딩이다 — 규칙으로 유도하면 `Detc`(대문자)나 `decc`→`PrecService`(판례와 같은 이름)에서 반드시 틀린다.

 - **OC 계정은 `메타` 에 없다** — 환경변수와 `.env` 에서 오고 DB 에 남기지 않는다.
"""

from __future__ import annotations

import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Callable, Iterable, Iterator, TypeVar

import httpx

HERE = Path(__file__).resolve().parent.parent

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ⚠️ **https 를 쓰지 마라.** 이 환경은 TLS 를 가로채고 시스템 CA 에만 그 인증서가 있어서
#    파이썬 번들 CA 로는 `CERTIFICATE_VERIFY_FAILED` 가 난다. 법제처 문서가 http 를 쓰고
#    이 계획의 6종은 전부 http 로 실측했다. `verify=False` 로 끄면 실패 원인이 영영 안 보인다.
검색 = "http://www.law.go.kr/DRF/lawSearch.do"
본문 = "http://www.law.go.kr/DRF/lawService.do"

# 문서상 상한은 100 인데 1000 이 동작한다(여섯 target 전수 확인). 문서에 없는 동작이라
# 언제 막혀도 이상하지 않다 — `검증.py` V4 가 감시한다.
최대페이지크기 = 1000

# 24 동시에서 24.15 req/s · 실패 1/96. **그 위는 안 재봤으니 올리지 마라**(§06 U6).
# 차단당하면 계정 단위로 막힌다.
기본유량 = 24.0
동시상한 = 24


class 자료종류:
    """DB 에 담는 여섯 종. CLI 도 문서도 이 한국어 이름만 쓴다(D10)."""

    법령 = "법령"
    판례 = "판례"
    헌재결정례 = "헌재결정례"
    행정심판례 = "행정심판례"
    법령해석례 = "법령해석례"
    행정규칙 = "행정규칙"

    전체 = ("법령", "판례", "헌재결정례", "행정심판례", "법령해석례", "행정규칙")


class 봉투정보:
    __slots__ = ("target", "목록봉투", "리스트키", "본문봉투", "식별자", "목록식별자")

    def __init__(self, target, 목록봉투, 리스트키, 본문봉투, 식별자, 목록식별자=None):
        self.target = target
        self.목록봉투 = 목록봉투
        self.리스트키 = 리스트키
        self.본문봉투 = 본문봉투
        self.식별자 = 식별자  # 본문 조회 파라미터 이름
        # ⚠️ 목록에서 그 식별자를 꺼낼 때 쓰는 **필드 이름**. 대개 본문과 같은데
        #    행정심판례만 다르다(목록 `행정심판재결례일련번호` / 본문 `행정심판례일련번호`).
        #    같다고 가정하면 None 을 ID 로 써서 본문 조회가 **전량** 실패한다 —
        #    목록 수집은 성공하므로 한참 뒤에야 드러난다.
        self.목록식별자 = 목록식별자


# ⚠️ **이 표를 규칙으로 대체하지 마라.** 2026-08-13 에 12개 값(목록봉투·리스트키·본문봉투
#    × 여섯 target)을 전수 재확인했다. `검증.py` V2·V3 이 이 표를 기준값으로 쓴다.
봉투: dict[str, 봉투정보] = {
    "법령": 봉투정보("law", "LawSearch", "law", "법령", "MST", "법령일련번호"),
    # ⚠️ `detc` 만 리스트 키가 대문자 `Detc` 다. target 을 그대로 키로 쓰는 코드가
    #    여기서만 조용히 빈 리스트를 반환한다.
    "헌재결정례": 봉투정보("detc", "DetcSearch", "Detc", "DetcService", "ID", "헌재결정례일련번호"),
    # ⚠️ `decc` 본문의 봉투가 `PrecService` 로 판례와 이름이 같다.
    #    **자료종류 판별은 언제나 요청한 target 으로 한다.**
    "행정심판례": 봉투정보("decc", "Decc", "decc", "PrecService", "ID", "행정심판재결례일련번호"),
    "판례": 봉투정보("prec", "PrecSearch", "prec", "PrecService", "ID", "판례일련번호"),
    "법령해석례": 봉투정보("expc", "Expc", "expc", "ExpcService", "ID", "법령해석례일련번호"),
    "행정규칙": 봉투정보("admrul", "AdmRulSearch", "admrul", "AdmRulService", "ID", "행정규칙일련번호"),
}

# 목록 요청에 자료종류마다 늘 붙는 것. 빠뜨리면 조용히 다른 모집단을 받는다.
목록기본: dict[str, dict[str, str]] = {
    # datSrcNm 을 안 걸면 169,938 건이 오고 그중 49,779 건은 본문 조회가 영구 실패한다(D4).
    "판례": {"datSrcNm": "대법원"},
    # ⚠️ nw 는 target 마다 뜻이 반대다. admrul 은 1=현행 2=연혁, eflaw 는 2=시행예정 3=현행.
    "행정규칙": {"nw": "1"},
}


class API오류(RuntimeError):
    """원천이 200 과 함께 돌려준 오류. `없음` 은 여기 오지 않는다."""


class 원천에없음(API오류):
    """원천이 **그 자료를 갖고 있지 않다**고 답한 것. 일시적 오류가 아니다.

    ⚠️ **재시도하지 마라.** 국세법령정보시스템 판례가 그 부류인데(HTTP 200 에
    `{"Law": "일치하는 판례가 없습니다…"}`) 매일 수만 건을 헛되이 두드리게 된다.
    `수집실패` 에 `'없음'` 으로 확정해야 감사가 그걸 구멍에서 제외한다.
    """


class 인증실패(API오류):
    """`{"result": "사용자 정보 검증에 실패하였습니다.", "msg": …}` — **HTTP 200 이다.**

    ⚠️ **이걸 만나면 즉시 전체를 멈춰라.** 계속 돌면 20만 건을 헛되이 두드리고,
    빈손 수집이 초록불로 끝난다.
    """


# 법제처의 인증은 별도 토큰이 아니라 **이메일 ID 앞부분**이다
# (`chunghun1@naver.com` → `chunghun1`). 무료이고 가입만 하면 누구나 받는다.
#
# **일부러 커밋해 둔다.** 감출 것이 아니라서다 — 이 값은 계획 문서(`01-원천-실측.md`,
# `06-미확인-사항.md`, `README.md`)에 이미 적혀 있고 레포는 공개다. 시크릿으로 두면
# 감춰지지도 않으면서 **새 기계마다 등록해야 하는 단계와 "키가 없어 빈손 수집"이라는
# 실패 유형만 는다.** 특히 셀프호스티드 러너는 자기 `_work` 아래 새 체크아웃에서 도는데
# `.env` 는 gitignore 라 거기 안 딸려 온다 — `.env` 에만 두면 자동 수집이 인증 실패한다.
#
# ⚠️ 대가는 하나 — 공개된 OC 로 누가 원천을 두드리면 **계정 단위로 막힐 수 있다**(§06 U6).
#    받아들이기로 한 위험이다. 막히면 `LAW_API_KEY` 환경변수로 다른 계정을 주면 된다.
기본OC = "chunghun1"


def load_key(env_path: Path | None = None) -> str:
    """`OC` 값. 환경변수 → `.env` → 기본값 순서다.

    앞의 둘은 **다른 계정으로 갈아타는 수단**이지 비밀을 넣는 수단이 아니다.
    """
    if key := os.environ.get("LAW_API_KEY"):
        return key
    path = env_path if env_path is not None else HERE / ".env"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("LAW_API_KEY=") and not line.startswith("#"):
                if 값 := line.split("=", 1)[1].strip().strip("\"'"):
                    return 값
    return 기본OC


def 행들(값) -> list[dict]:
    """단수/복수를 하나로 만든다.

    ⚠️ **결과가 1건이면 리스트 키의 값이 `list` 가 아니라 `dict` 다.** `display=1` 로
    개발하고 `display=1000` 으로 운영하면 개발 중엔 안 터지고 운영에서 터진다 —
    반대도 마찬가지다. 모든 배열 필드에 이걸 통과시켜라.
    """
    if 값 is None:
        return []
    if isinstance(값, dict):
        return [값]
    if isinstance(값, list):
        return [x for x in 값 if isinstance(x, dict)]
    return []


def 인증확인(payload: dict, 무엇: str) -> dict:
    """`{"result": …, "msg": …}` 만 가려낸다. **다른 봉투를 안 쓰는 경로도 이건 써야 한다.**

    ⚠️ **인증 실패도 HTTP 200 이다.** 이 검사를 건너뛴 요청 경로가 하나라도 있으면
    그쪽은 실패를 빈 응답으로 읽고, **빈손 수집이 초록불로 끝난다.** 여섯 자료종류의
    봉투를 안 쓰는 자료(`lsDelegated` 등)가 `분류()` 대신 이걸 부른다.
    """
    if "result" in payload and "msg" in payload:
        raise 인증실패(f"{무엇}: {payload.get('result')}")
    return payload


def 분류(payload: dict, 종류: str, 단계: str) -> dict:
    """응답 봉투를 열고, 열 수 없으면 왜 못 여는지를 예외로 만든다.

    **HTTP 상태 코드로는 아무것도 판별할 수 없다.** 인증 실패도 자료 부재도 전부 200 이다.

    | 봉투 | 뜻 |
    |---|---|
    | `{"result": …, "msg": …}` | 인증 실패 → `인증실패` (전체 중단) |
    | `{"Law": "…없습니다…"}` | 원천에 없다 → `원천에없음` (재시도 금지) |
    | 기대 봉투 없음 | 형식이 바뀌었다 → `API오류` |
    """
    인증확인(payload, f"{종류}/{단계}")

    정보 = 봉투[종류]
    기대 = 정보.목록봉투 if 단계 == "목록" else 정보.본문봉투
    if (몸통 := payload.get(기대)) is not None:
        return 몸통

    # ⚠️ 본문이 없을 때 오는 형태. 최상위 키가 `Law` 이고 값이 **문자열**이다.
    #    키 이름이 `Law` 라고 법령 자료인 것이 아니다 — 판례 조회에도 이게 온다.
    if isinstance(payload.get("Law"), str):
        raise 원천에없음(f"{종류}/{단계}: {payload['Law'].strip()[:60]}")
    if len(payload) == 1 and isinstance(next(iter(payload.values())), str):
        raise 원천에없음(f"{종류}/{단계}: {next(iter(payload.values())).strip()[:60]}")

    raise API오류(f"{종류}/{단계}: 봉투 {기대!r} 가 없다 — 받은 키 {list(payload)}")


class Pacer:
    """초당 요청 수의 상한. **여러 스레드가 공유하므로 락이 필요하다.**

    congress 의 같은 이름 클래스는 직렬 전제라 락이 없다. 여기서 그대로 베끼면 24개
    워커가 각자 자기 시계를 보고 동시에 나가 유량 제한이 사실상 사라진다.
    """

    def __init__(self, rate: float = 기본유량):
        self.간격 = 1.0 / rate if rate > 0 else 0.0
        self._다음 = 0.0
        self._락 = threading.Lock()

    def wait(self) -> None:
        if not self.간격:
            return
        with self._락:
            지금 = time.monotonic()
            출발 = max(지금, self._다음)
            self._다음 = 출발 + self.간격
        if (남은 := 출발 - 지금) > 0:
            time.sleep(남은)


T = TypeVar("T")
R = TypeVar("R")


def 워커수(rate: float) -> int:
    """손잡이 하나(`rate`)에서 워커 수를 정한다.

    단건 응답이 약 0.83초라 직렬로는 초당 1.2건이 상한이고, 20만 건이면 46시간이 된다.
    **이 도메인에서 동시성은 선택이 아니라 필수인데**, 그렇다고 손잡이를 둘 두면
    상호작용을 사람이 계산하게 된다.
    """
    return max(1, min(동시상한, round(rate) or 1))


def 맵(fn: Callable[[T], R], 항목들: Iterable[T], 워커: int) -> Iterator[R]:
    """워커 수만큼 동시에 돌리고 **완료 순서대로** 흘린다.

    `executor.map` 을 쓰지 않는 이유는 그쪽이 **입력 순서**로 내주기 때문이다. 느린 한
    건이 뒤의 완료된 것들을 붙잡고 있으면 메인 스레드의 DB 쓰기가 그동안 멈춘다.
    """
    항목들 = list(항목들)
    if not 항목들:
        return
    if 워커 <= 1:
        for x in 항목들:
            yield fn(x)
        return
    with ThreadPoolExecutor(max_workers=워커) as ex:
        futs = [ex.submit(fn, x) for x in 항목들]
        for f in as_completed(futs):
            yield f.result()


진행간격 = 15.0


def 진행자(로그, 라벨: str, 전체: int | None = None, c: "Client | None" = None,
          간격: float | None = None, 시계=time.monotonic):
    """`진행(현재)` 를 돌려준다. **마지막 출력 후 `간격` 초가 지났을 때만** 한 줄 찍는다.

    ⚠️ **건수 기준("N건마다")으로 쓰지 마라.** 한 건이 느린 구간에서 몇 분씩 조용하고,
    그러면 Actions 로그만 보고는 **러너가 죽은 것과 도는 것을 구별할 수 없다.**
    러너가 죽으면 `upload-artifact` 가 403 으로 실패해 파일 로그가 통째로 사라지므로
    (실측 2026-08-19), 남는 것은 stdout 으로 흘린 것뿐이다.
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
        로그("    " + " · ".join(조각))

    return 진행


class Client:
    """유량·동시성·재시도를 한 곳에 둔다. 이걸 거치지 않는 요청 경로를 만들지 마라.

    **손잡이는 `rate` 하나다.** 워커 수는 거기서 정해진다 — 단건 응답이 약 0.83초라
    직렬로는 초당 1.2건이 상한이고, 20만 건이면 46시간이 된다. 즉 이 도메인에서
    동시성은 선택이 아니라 필수인데, 그렇다고 손잡이를 둘 두면 상호작용을 사람이
    계산하게 된다.

    ⚠️ **DB 를 이 안에서 만지지 마라.** 워커는 받아서 파싱만 하고, 쓰기는 부르는 쪽
    메인 스레드가 한다. sqlite3 연결은 스레드를 넘길 수 없다.
    """

    def __init__(
        self,
        rate: float = 기본유량,
        timeout: float = 60.0,
        retries: int = 5,
        oc: str | None = None,
        로그=None,
    ):
        self.pacer = Pacer(rate)
        # 목록 전량 조회가 스스로 진행을 알린다. **가장 긴 침묵이 여기다** — 실측으로
        # 판례 목록 하나가 2분 18초를 조용히 돌았다. 기본은 None(조용)이다.
        self.로그 = 로그
        self.워커 = 워커수(rate)
        self.retries = retries
        self.backoff_횟수 = 0
        self.요청수 = 0
        self.oc = oc or load_key()
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

    # ── 저수준 ────────────────────────────────────────────────

    def _보낸다(self, url: str, params: dict) -> httpx.Response:
        """HTTP 오류는 **전부** 일시적 장애로 보고 재시도한다. 4xx 도 그렇다.

        ⚠️ **원천은 '없다'를 4xx 로 말하지 않는다.** 없는 ID 를 물으면 HTTP 200 에
        `{"Law": "일치하는 판례가 없습니다…"}` 가 온다(실측 2026-08-28, 본문 네 종류 전부).
        부재는 봉투로 오므로 그 판정은 `분류()` 가 하고, 여기까지 온 4xx 는 원천의 답이
        아니라 그 앞의 인프라다.

        실측이 양쪽에 다 있다 — 목록에서는 `eflaw&nw=2` 404 로 수집이 통째로 죽었고
        같은 요청이 곧바로 6/6 성공했다. 본문에서는 4xx 를 '없음'으로 확정한 탓에
        **원천에 멀쩡히 있는 506건이 다시는 요청되지 않는 상태로 굳어 있었다**
        (표본 5/5 가 지금 정상 응답한다). 하나는 죽고 하나는 조용했을 뿐 원인은 같다.
        """
        마지막 = None
        for 시도 in range(self.retries):
            self.pacer.wait()
            self.요청수 += 1
            try:
                r = self._c.get(url, params=params)
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"HTTP {r.status_code}", request=r.request, response=r
                    )
                return r
            except (httpx.HTTPError, httpx.StreamError) as e:
                마지막 = e
                if 시도 == self.retries - 1:
                    break
                self.backoff_횟수 += 1
                # 지수 백오프 + 지터. 지터가 없으면 여러 실패가 같은 순간에 몰려 재개된다.
                time.sleep(min(30.0, 2**시도) * (0.5 + random.random()))
        raise API오류(f"{url}: {type(마지막).__name__}: {마지막}") from 마지막

    def _json(self, url: str, params: dict) -> dict:
        # ⚠️ `type=JSON` 을 빠뜨리면 HTTP 200 에 JSON 이 아닌 것이 온다.
        #    예외가 아니라 파싱 실패로 뒤늦게 나타나므로 여기서 못박는다.
        params = {"OC": self.oc, "type": "JSON", **params}
        r = self._보낸다(url, params)
        try:
            payload = r.json()
        except ValueError as e:
            raise API오류(f"{url}: JSON 이 아니다 — {r.text[:80]!r}") from e
        if not isinstance(payload, dict):
            raise API오류(f"{url}: 최상위가 dict 가 아니다 — {type(payload).__name__}")
        return payload

    # ── 목록 ──────────────────────────────────────────────────

    def 목록페이지(self, 종류: str, page: int, **params) -> tuple[list[dict], int]:
        """목록 한 페이지. `(행, 총건수)` 를 준다."""
        정보 = 봉투[종류]
        q = {
            "target": 정보.target,
            "display": 최대페이지크기,
            "page": page,
            **목록기본.get(종류, {}),
            **params,
        }
        몸통 = 분류(self._json(검색, q), 종류, "목록")
        총 = int(몸통.get("totalCnt") or 0)
        # ⚠️ 0건이면 리스트 키 **자체가 없다**. `몸통[리스트키]` 는 KeyError 다.
        return 행들(몸통.get(정보.리스트키)), 총

    def 총건수(self, 종류: str, **params) -> int:
        return self.목록페이지(종류, 1, display=1, **params)[1]

    def 목록전량(
        self, 종류: str, 행키: tuple[str, ...] | None = None, **params
    ) -> tuple[list[dict], bool]:
        """전량 열거. `(행들, 완결인가)` 를 준다.

        ⚠️ **행이 빌 때까지 도는 방식을 쓰지 마라.** 중간 페이지가 일시적으로 빈손이면
        거기서 멈추고 **성공으로 끝난다.** 총건수를 믿고 정확히 그만큼 돈다.

        ⚠️ **그런데 총건수를 믿기만 해도 안 된다.** 중간 페이지가 짧게 오거나 서버가
        `display` 를 100 으로 되돌리면 **덜 받고도 다 받은 것처럼 끝난다.** 그 목록으로
        "목록에 없는 행 정리"를 하면 **멀쩡한 수집물을 지운다.** 그래서 받은 고유 건수가
        `totalCnt` 와 정확히 같을 때만 완결로 표시하고, 부르는 쪽이 그때만 정리한다.

        ⚠️ **`행키` 는 "이 목록에서 한 행을 유일하게 만드는 것"이다.** 기본값은
        `목록식별자` 인데, 그게 맞지 않는 목록이 있다 — `eflaw&nw=2`(시행예정)는
        **한 버전을 시행일마다 한 행씩** 준다(실측 1,020행 · MST 고유 937 ·
        `(MST, 시행일자)` 고유 1,020). 그때 MST 로 세면 **영원히 불완결**이 되고,
        정리·승격이 조용히 멈춘 채 감사만 빨개진다. 실제로 그렇게 굳어 있었다.
        **행이 중복인 것과 행을 세는 키가 틀린 것은 다르다.**
        """
        정보 = 봉투[종류]
        키들 = 행키 or (정보.목록식별자,)
        rows, 총 = self.목록페이지(종류, 1, **params)
        모음 = list(rows)
        # 이 메서드는 최소 스탠드인 self 로도 불린다(테스트·다른 호출 경로).
        로그 = getattr(self, "로그", None)
        진행 = 진행자(로그, f"목록 {종류}", 총, self) if 로그 else None
        for p in range(2, -(-총 // 최대페이지크기) + 1):
            더, _ = self.목록페이지(종류, p, **params)
            모음.extend(더)
            if 진행:
                진행(len(모음))
        고유 = {
            tuple(str(r.get(k)) for k in 키들) for r in 모음 if r.get(키들[0])
        }
        return 모음, len(고유) == 총 and 총 > 0

    # ── 본문 ──────────────────────────────────────────────────

    def 본문하나(self, 종류: str, 식별값: str, **params) -> dict:
        정보 = 봉투[종류]
        q = {"target": 정보.target, 정보.식별자: 식별값, **params}
        return 분류(self._json(본문, q), 종류, "본문")

    def 본문여럿(
        self, 종류: str, 식별값들: Iterable[str], **params
    ) -> Iterator[tuple[str, dict | None, Exception | None]]:
        """동시에 받아 **완료 순서대로** `(식별값, 몸통, 예외)` 를 흘린다.

        ⚠️ **한 건의 실패로 배치를 버리지 마라.** 20만 건 수집에서 배치 폐기는 시간을
        몇 배로 늘린다. 실패는 자료 단위로 격리해 예외를 그대로 넘기고, 부르는 쪽이
        `수집실패` 에 분류해 적는다.

        ⚠️ **`인증실패` 만은 예외다.** 그건 전체가 잘못된 것이므로 그대로 올린다.
        """
        def 하나(v: str):
            try:
                return v, self.본문하나(종류, v, **params), None
            except 인증실패:
                raise
            except Exception as e:  # noqa: BLE001 — 부르는 쪽이 분류한다
                return v, None, e

        yield from 맵(하나, 식별값들, self.워커)
