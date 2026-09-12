#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""LAW·CONGRESS 를 세대로 뜬다. **이 레포에 사본은 이것뿐이다.**

두 DB 는 합쳐 4.3GiB 이고 `.gitignore` 에 막혀 있다. 이 파일이 생기기 전까지 **세상에 사본이
하나도 없었다** — 스냅숏도 Time Machine 도 없는데 매 실행이 라이브 DB 를 직접 고치고,
법령은 컬럼 DROP 을 포함한 이행을 그 위에서 돈다. LAW.db 는 수 시간짜리 전량 수집의
결과물이라 잃으면 되받는 데 하루가 든다.

**승격을 가르는 것은 구조 검증이고, 코퍼스 감사는 세대에 붙는 라벨이다.** 둘을 같은 문으로
만들면 안 된다 — 실측(2026-08-28) LAW 는 게이트 2건이 빨간 상태였다. 감사로 승격을 막았다면
**바로 그날, 가장 백업이 필요한 날에 사본이 0개**가 된다. 손상된 DB 의 사본도 사본이다;
거짓말하지 않고 라벨을 붙이는 것이 답이고, 그 라벨을 읽어 거절할지는 `이행()` 이 정한다.

**`cp` 도 `.backup` 도 아니고 `VACUUM INTO` 다.** WAL 모드라 `cp` 는 찢어진 파일을 만든다.
`.backup` 은 100페이지마다 잠금을 놓았다 잡는데 그 사이 외부가 쓰면 **복사를 처음부터 다시
시작해서** 쓰기가 잦은 DB 에서 수렴하지 않는다(옆 레포 Naver-News 가 5.3GB 에서 실측으로
겪고 갈아탔다). `VACUUM INTO` 는 읽기 트랜잭션 하나 안에서 끝나 재시작 자체가 없다.
실측(2026-08-28): LAW 3.65GiB 17초 · CONGRESS 638MiB 1초.

⚠️ **원본은 `mode=ro`, 사본은 `immutable=1`. 바꾸면 둘 다 깨진다.** 원본을 읽기·쓰기로 열면
   SQLite 가 닫으며 WAL 체크포인트를 돌려 **백업 잡이 백업 대상을 수정한다.**
   (`query_only=ON` 은 대안이 아니다 — SQL 쓰기만 막고 그 체크포인트는 못 막는다.)
   거꾸로 원본에 `immutable` 을 쓰면 WAL 을 무시해 **최근 커밋이 빠진 것을 뜬다.**
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
# 여기서 세는 것은 **스킬 디렉터리**다 — 아래 코퍼스 명세가 `Scripts/…` 를 이어 붙이고,
# 감사를 부를 때 그 상대경로를 `cwd` 로 쓴다.
스킬 = Path(__file__).resolve().parents[1]

# 사다리는 내장 디스크에, 최신 몇 세대만 iCloud 로 반출한다. 한 세대가 4.3GiB 이고
# iCloud 잔여가 24.2GiB 라(실측 2026-08-28) 사다리 전체는 애초에 안 들어간다 —
# 반출을 최신 2세대로 좁히는 대신 사다리를 깊게 가져가는 것이 그 제약 아래의 선택이다.
# ⚠️ 레포 안에 두지 마라. `actions/checkout` 이 `git clean -ffdx` 를 돈다.
기본보관 = Path.home() / "Library/Application Support/Congress-DB/백업"
기본반출 = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/백업/Congress-DB"

보관일 = 7          # 최근 이만큼의 날짜
보관월 = 12         # 매월 1일자 중 최신 이만큼
반출세대 = 2        # iCloud 로 내보낼 최신 세대 수
이행직전보존일 = 90  # 이행 직전본은 사다리 규칙과 무관하게 이만큼 붙잡는다
여유바이트 = 10 * 1024**3


@dataclass(frozen=True)
class 명세:
    """한 코퍼스를 백업하는 데 필요한 전부. **인자로 받을 수 있는 것을 상수로 박지 않는다** —
    `db경로` 가 함수인 이유가 그것이다. 수집기가 겨누는 파일과 백업이 겨누는 파일이 갈리면
    **백업은 매일 초록인데 정작 쓰이는 DB 는 사본이 없다.** 같은 함수를 거치면 갈릴 수 없다."""

    이름: str
    스크립트: Path
    감사스크립트: str
    핵심표: tuple[str, ...]
    # **일이 끝나면 줄어드는 표.** 급감 비교에서 빼는 것이 아니라 **판정에서만** 뺀다 —
    # `급감표()` 주석이 그 구별을 진다.
    원장표: tuple[str, ...] = ()

    def db경로(self, 명시=None) -> Path:
        # 수집기 자신의 해석기를 빌린다. 환경변수 이름도 기본값도 저쪽이 소유한다.
        return _모듈(self.스크립트).db_path(명시)


_모듈캐시: dict[Path, object] = {}


def _모듈(스크립트: Path):
    """수집기 모듈을 그 스크립트 디렉터리를 sys.path 에 넣고 불러온다.

    ⚠️ **`db_path` 를 여기서 다시 구현하지 마라.** 환경변수 이름(`LAW_DB`·`CONGRESS_DB`)과
       기본 경로는 저쪽이 소유한다. 두 곳에 적으면 한쪽만 고치게 되고, 그 순간 백업은
       존재하지 않는 DB 를 뜨거나 엉뚱한 DB 를 뜬다.
    """
    if 스크립트 not in _모듈캐시:
        import importlib.util

        sys.path.insert(0, str(스크립트.parent))
        # ⚠️ **이름을 네임스페이스로 감싸고 `sys.modules` 에 등록한 뒤에 실행한다.** 둘 다
        #    필요하다. 등록하지 않으면 모듈 안의 `@dataclass` 가 `sys.modules[__module__]`
        #    를 찾다 `None` 을 만나 `AttributeError` 로 죽는데, 그 오류는 데이터클래스와
        #    아무 상관없어 보인다. 감싸지 않으면 `db` 라는 흔한 이름이 congress 자신의
        #    테스트가 import 하는 `db` 를 조용히 가린다.
        이름 = f"백업_{스크립트.parent.parent.name}_{스크립트.stem}"
        spec = importlib.util.spec_from_file_location(이름, 스크립트)
        m = importlib.util.module_from_spec(spec)
        sys.modules[이름] = m
        try:
            spec.loader.exec_module(m)
        except BaseException:
            del sys.modules[이름]
            raise
        _모듈캐시[스크립트] = m
    return _모듈캐시[스크립트]


# 인자 공간이 곧 문서다 — 코퍼스가 둘뿐이라는 것을 `--help` 가 말하므로 어디에도 적지 않는다.
코퍼스: dict[str, 명세] = {
    "law": 명세(
        이름="LAW",
        스크립트=스킬 / "Scripts/법제처/적재.py",
        감사스크립트="Scripts/법제처/감사.py",
        # ⚠️ **"행이 하나라도 있나"를 여기서 묻는다.** 사본이 quick_check 를 통과해도
        #    내용이 비었으면 백업이 아니다. 진짜 하한(전회 대비 감소율)은 아래 `_비교` 가 진다.
        핵심표=("법령", "조문", "판례", "헌재결정례", "행정심판례", "법령해석례", "행정규칙"),
        # ⚠️ **실측 오탐이 확인된 둘만 넣는다.** `수집실패 507→1`·`정리후보 15→2` 가
        #    A27 을 빨갛게 만든 그 표들이다. `파싱실패`·`수집상태` 는 넣지 않는다 —
        #    파서를 고친 날에만 줄고, 그날 한 번 빨간 것이 이 축을 끄는 것보다 낫다.
        #    나중에 오탐이 실제로 나면 그때 R24 줄이 근거를 준다.
        # ⚠️ **`수집실패` 를 빼도 해로운 절반은 A1 이 잡는다** — `'없음'` 으로 확정된 행이
        #    사라지면 `본문 + 없음 = 목록` 등식이 그만큼 어긋나 빨개진다. 남는 절반
        #    (`재시도`·`막힘` 행 소실)은 다음 수집이 다시 시도해 스스로 메우므로 사고가
        #    아니다. 지금 이 표는 1행이라, 빼지 않으면 `1 → 0` 이 매번 100% 급감이다.
        원장표=("정리후보", "수집실패"),
    ),
    "congress": 명세(
        이름="CONGRESS",
        스크립트=스킬 / "Scripts/국회/db.py",
        감사스크립트="Scripts/국회/audit.py",
        핵심표=("의원", "의안", "회의", "발언", "표결"),
    ),
}

# `#N` 은 같은 날 두 번째 이후의 세대다 — 성한 사본을 덮지 않으려고 옆에 올린 것이라
# (`_승격자리` 참조) 이름 규칙이 그걸 표현할 수 있어야 한다.
세대이름 = re.compile(
    r"^(?P<이름>[A-Z]+)-(?P<날짜>\d{4}-\d{2}-\d{2})(?P<꼬리>-이행직전)?"
    r"(?:#(?P<차수>\d+))?\.db$"
)


# ── 세대 ────────────────────────────────────────────────────────────────────


@dataclass
class 세대:
    경로: Path
    이름: str
    날짜: str
    이행직전: bool
    기록: dict = field(default_factory=dict)
    차수: int = 1

    @property
    def 검증(self) -> bool:
        """구조 검증을 통과했나. **감사와 다른 질문이다** — 이건 "사본이 온전한가"이고
        감사는 "내용이 말이 되나"다. 사본이 온전하지 않으면 애초에 승격되지 않으므로
        여기 있는 세대는 전부 참이어야 한다; 거짓이면 사람이 손으로 놓은 파일이다."""
        return bool(self.기록.get("검증"))

    @property
    def 감사통과(self) -> bool:
        """⚠️ **모르는 것은 통과가 아니다.** 감사를 건너뛴 세대(`--감사-생략`)와 감사가
        죽은 세대는 여기서 거짓이어야 한다 — `정리()` 가 "마지막 성한 사본"을 고르는 데
        이 값을 쓰므로, 모름을 통과로 읽으면 진짜 성한 사본이 밀려난다."""
        return self.기록.get("감사통과") is True

    @property
    def 곁(self) -> Path:
        return self.경로.with_suffix(".json")


def 세대들(명세_: 명세, 보관=None) -> list[세대]:
    """디렉터리를 읽어 최신순으로 준다. **파일이 진실이고 곁기록은 주석이다.**

    ⚠️ 장부 파일 하나에 목록을 모으지 않는 이유 — 사람이 사본을 지우면 장부만 남아
       "있다"고 답한다. 그 거짓말은 정확히 복구가 필요한 순간에 드러난다.
    """
    보관 = Path(보관 or os.environ.get("CORPUS_BACKUP_DIR") or 기본보관)
    if not 보관.is_dir():
        return []
    나온것 = []
    for p in 보관.iterdir():
        m = 세대이름.match(p.name)
        if not (m and p.is_file() and not p.is_symlink() and m["이름"] == 명세_.이름):
            continue
        곁 = p.with_suffix(".json")
        기록 = {}
        if 곁.is_file():
            try:
                기록 = json.loads(곁.read_text())
            except ValueError:
                pass
        나온것.append(세대(p, m["이름"], m["날짜"], bool(m["꼬리"]), 기록,
                          int(m["차수"] or 1)))
    나온것.sort(key=lambda g: (g.날짜, g.차수, g.이행직전), reverse=True)
    return 나온것


def 최신검증백업(코퍼스명: str, 보관=None) -> 세대 | None:
    """구조 검증을 통과한 가장 최근 세대. **`이행()` preflight 이 이걸 묻는다.**

    ⚠️ **DB 안의 메모를 믿지 말고 파일을 봐라.** 「마지막으로 백업했다」를 DB 메타에 적어
       두면 사본을 지운 뒤에도 그 메모가 남아 파괴적 이행을 통과시킨다. 여기서 굳이
       디렉터리를 훑는 비용을 내는 이유가 그것이다.
    """
    for g in 세대들(코퍼스[코퍼스명], 보관):
        if g.검증:
            return g
    return None


# ── 감사가 견줄 '직전 정상' ──────────────────────────────────────────────────
#
# ⚠️ **기준선은 DB 밖에 있어야 한다.** 감사 게이트는 전부 "현재 DB 안에서 등식이 성립하는가"
#    를 묻는데, 행이 사라지면 등식의 양변이 함께 사라져 **오히려 잘 맞는다.** 실측
#    (2026-08-28): 빈 국회 DB 에서 게이트 13개 중 12개가 초록이고, 의안 한 건만 남기면
#    13개 전부 초록이다. 기준선을 DB 안에 두면 DB 를 통째로 갈아치운 사고에서 기준선도
#    같이 사라지므로 같은 구멍이 남는다. 세대 곁기록이 이 레포에서 유일하게 밖에 산다.
#
# 20% 는 대참사의 문턱이지 변동의 문턱이 아니다 — 실측으로 가장 컸던 정상 감소가 하루
# 0.9%(판례를 원천이 거둬 갔다)이고, 잡으려는 것은 99.99% 손실이다. 쓰기 시점의 촘촘한
# 가드(법령 정리 2% · 국회 교체 10%)가 앞에 서 있고 **이건 마지막 그물이라 넓어야 한다** —
# 여기가 자주 울리면 아무도 안 보게 되고, 그때는 대참사도 못 잡는다.
급감기준 = 0.20


def 기준선(코퍼스명: str, 보관=None) -> tuple[str, dict[str, int]] | None:
    """직전 검증 세대의 표별 행수. 견줄 것이 없으면 `None`.

    ⚠️ **`None` 은 위반이 아니다.** 첫 백업 전에도 수집은 돌아야 하고, 여기서 빨개지면
    가드가 스스로를 영구화한다. 다만 **기준선이 없어서 0인 것과 견줘 봐서 0인 것은 다른
    사건이므로**, 부르는 쪽이 그 둘을 구별해 보여야 한다 — 안 그러면 백업이 멈춘 날
    감사가 조용히 아무것도 안 지킨다.

    ⚠️ **최신 검증본이 아니라 최신 감사통과본이다.** 승격을 가르는 것은 구조 검증이고
    감사는 라벨이라, 코퍼스가 망가진 날의 사본도 정상적으로 승격된다(그게 옳다 — 가장
    백업이 필요한 날 사본이 0개가 되면 안 된다). 그런데 그 손상본을 기준선으로 삼으면
    **사고 다음 백업 한 번으로 급감이 0 이 되어 사고가 자기 증거를 지운다.**

    ⚠️ **감사통과본이 없으면 검증본으로 물러난다.** 물러나지 않으면 감사가 한 번도 통과한
    적 없는 코퍼스에서 그물이 아예 없어진다(법령이 실제로 그 상태였다). 옛 기준선은
    급감만 재므로 **더 엄격할 뿐 거짓 초록을 만들지 않는다.** 다만 무엇에 견줬는지는
    이름에 실어 보낸다 — 읽는 사람이 그 차이를 알아야 한다.
    """
    쓸만한 = [g for g in 세대들(코퍼스[코퍼스명], 보관) if g.검증 and g.기록.get("행수")]
    for g in 쓸만한:
        if g.감사통과:
            return g.경로.name, g.기록["행수"]
    if 쓸만한:
        g = 쓸만한[0]
        return f"{g.경로.name}(감사 미통과본)", g.기록["행수"]
    return None


def 급감표(코퍼스명: str, 전: dict[str, int],
         지금: dict[str, int]) -> list[tuple[str, int, int, bool]]:
    """직전 정상보다 `급감기준` 넘게 줄어든 표들. 마지막 자리가 **원장인가**다.

    ⚠️ **표가 통째로 없어진 것은 여기서 안 센다** — 그건 감사 A0(스키마 드리프트)가 이미
    빨갛게 낸다. 두 곳이 같은 것을 세면 한 사고가 두 번 보이고, 어느 쪽이 진짜인지를
    사람이 매번 다시 판단하게 된다.

    ⚠️ **원장을 목록에서 빼지 않고 표지만 단다.** 원장(`정리후보`·`수집실패`)은 일이 끝나면
    줄어드는 표라 급감으로 세면 **가장 잘 돌아간 날이 가장 빨갛다** — 실측으로
    `수집실패 507→1`·`정리후보 15→2` 가 A27 을 빨갛게 만들었고 핵심표는 평평했다.
    그렇다고 여기서 걸러 버리면 **빨강만이 아니라 R24·R15 의 상세에서도 사라져** 그 표에
    대해 축이 통째로 꺼진다. 곁기록의 `행수` 는 감사가 DB **밖**에서 잡는 유일한 축이고,
    그것을 조용히 끄는 것은 게이트를 무르게 하는 것과 다른 종류의 손실이다.
    **판정은 부르는 쪽이 이 표지를 보고 가른다.**
    """
    원장 = set(코퍼스[코퍼스명].원장표)
    return [
        (t, 옛, 지금[t], t in 원장)
        for t, 옛 in 전.items()
        if 옛 and t in 지금 and 지금[t] >= 0 and 지금[t] < 옛 * (1 - 급감기준)
    ]


def 신선한가(g: 세대 | None, 시간: float = 24.0) -> bool:
    if not g:
        return False
    찍힘 = g.기록.get("생성시각")
    if not 찍힘:
        return False
    return datetime.now(KST) - datetime.fromisoformat(찍힘) < timedelta(hours=시간)


def 이행전_확인(코퍼스명: str, db경로, *, 시간: float = 24.0, 보관=None) -> 세대:
    """**파괴적인 것 앞에 서는 문.** 신선한 검증본이 없으면 통과시키지 않는다.

    `db경로` 는 **지금 고치려는 바로 그 파일**이다 — 호출자가 `conn` 이 실제로 연 파일을
    물어서 넘긴다. ⚠️ 이 인자가 없으면 문이 **다른 DB 의 백업으로 열린다**: A 를 백업한 뒤
    `LAW_DB` 를 B 로 바꾸면 A 의 신선한 사본을 근거로 B 를 파괴한다. 코퍼스 이름은 어느
    사다리를 볼지만 정하고, 무엇을 지키는지는 경로가 정한다.

    이행은 컬럼 DROP 과 테이블 재구축을 하고 **되돌릴 방법이 없다.** 테이블 단위
    트랜잭션은 있지만 상위 트랜잭션이 없어서, 중간에 끊기면 앞서 지운 컬럼은 돌아오지
    않는다. 그러니 되돌릴 수단을 손에 쥐기 전에는 시작하지 않는 것이 유일한 방어다.

    ⚠️ **`락없음` 세대를 받지 않는다.** 락을 못 잡고 뜬 사본은 "이행 도중이 아니다"를
       보장하지 못한다 — 반쯤 이행된 상태의 사본을 근거로 또 이행에 들어가면, 되돌릴 곳이
       되돌릴 수 없는 상태다.

    ⚠️ **부르는 자리가 중요하다 — 실제로 옮길 것이 있을 때만 불러라.** 이행이 멱등이라
       평소에는 아무것도 안 하는데, 그 no-op 앞에서도 백업을 요구하면 **백업이 하루 밀린
       날 수집 전체가 멈춘다.** 문은 파괴적인 경로에만 단다.
    """
    g = 최신검증백업(코퍼스명, 보관)
    이름 = 코퍼스[코퍼스명].이름
    if not g:
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — 검증된 백업이 하나도 없다.\n"
            f"   `uv run Scripts/백업.py {코퍼스명} --이행직전` 을 먼저 돌려라."
        )
    if not 신선한가(g, 시간):
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — 가장 최근 검증본이 {g.날짜} 로 {시간:.0f}시간보다 낡았다.\n"
            f"   `uv run Scripts/백업.py {코퍼스명} --이행직전` 을 먼저 돌려라."
        )
    if not g.기록.get("락잡음"):
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — {g.경로.name} 은 수집 락 없이 뜬 사본이라\n"
            f"   '이행 도중이 아니다'를 보장하지 못한다. 수집이 끝난 뒤 다시 떠라."
        )

    # ⚠️ **백업이 이 DB 의 것인지 본다.** 곁기록의 `원본` 과 지금 고치려는 파일이 다르면
    #    그 사본으로는 되돌릴 수 없다. 실패 후 A 를 복원해도 B 는 돌아오지 않는다.
    지금 = Path(db경로).resolve()
    떴던것 = Path(g.기록.get("원본", "")).resolve() if g.기록.get("원본") else None
    if 떴던것 != 지금:
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — 백업은 다른 파일의 것이다.\n"
            f"   지금 고치려는 것: {지금}\n"
            f"   {g.경로.name} 이 뜬 것: {떴던것}"
        )

    # ⚠️ **곁기록은 과거의 주장이지 지금의 상태가 아니다.** 사본이 저장장치 사고로 잘려도
    #    JSON 의 `"검증": true` 는 그대로 남는다. 되돌릴 수 없는 일을 시작하기 직전이므로
    #    **지금 실제로 성한지** 다시 잰다. 이행은 드물게 도는 경로라 3.6GiB 지문(2~3초)이
    #    아깝지 않다 — 여기서 아낀 몇 초가 코퍼스 전체와 맞바꿔진다.
    if not g.경로.is_file():
        raise SystemExit(f"🔴 {이름} 이행을 거부한다 — 사본이 사라졌다: {g.경로}")
    if (있음 := g.경로.stat().st_size) != (적힘 := g.기록.get("바이트")):
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — 사본 크기가 기록과 다르다"
            f" (지금 {있음:,} · 기록 {적힘:,})"
        )
    if (적힌지문 := g.기록.get("sha256")) and _해시(g.경로) != 적힌지문:
        raise SystemExit(
            f"🔴 {이름} 이행을 거부한다 — 사본 지문이 뜰 때와 다르다: {g.경로.name}"
        )
    try:
        c = sqlite3.connect(f"file:{g.경로}?immutable=1", uri=True)
        try:
            if (qc := c.execute("PRAGMA quick_check(1)").fetchone()[0]) != "ok":
                raise SystemExit(f"🔴 {이름} 이행을 거부한다 — 사본이 성하지 않다: {qc}")
        finally:
            c.close()
    except sqlite3.Error as e:
        raise SystemExit(f"🔴 {이름} 이행을 거부한다 — 사본이 열리지 않는다: {e}") from e
    return g


def 연파일(conn) -> Path:
    """`conn` 이 **실제로 연** 파일. 환경변수나 인자가 아니라 연결에 물어본다 —
    그래야 문이 지키는 대상과 고쳐지는 대상이 갈릴 수 없다."""
    for _, 이름, 파일 in conn.execute("PRAGMA database_list"):
        if 이름 == "main":
            return Path(파일)
    raise RuntimeError("main 데이터베이스를 찾지 못했다")


def 점검(코퍼스명: str, *, db=None, 보관=None, 로그=print) -> int:
    """수집 **전에** 원본이 우리가 아는 그 파일인지 본다. 위반 수를 돌려준다.

    ⚠️ **유령 DB 를 막는 것이 목적이다.** `connect()` 가 부모 디렉터리를 만들고 일반
       모드로 열기 때문에, 폴더를 옮긴 날 자동 수집은 **새 빈 DB 를 만들고 계속 초록**이며
       의원실이 실제로 보는 DB 는 조용히 낡는다. 아무 에러도 나지 않는다.

    ⚠️ **최소 크기를 상수로 박지 않는다.** 코퍼스가 자라면 그 상수는 매번 낡고, 낡은 상수는
       아무것도 안 잡는다. 직전 세대와 견주는 것이 6단계의 '직전 정상과 비교한다'와 같은 축이다.
    """
    명세_ = 코퍼스[코퍼스명]
    기대 = Path(db) if db else 명세_.db경로()
    위반: list[str] = []

    if not 기대.exists():
        위반.append(f"DB 가 없다: {기대}")
    elif not 기대.is_file():
        위반.append(f"DB 가 일반 파일이 아니다: {기대}")
    else:
        # ⚠️ **`resolve()` 결과와 원본 경로를 비교하면 안 된다** — 상대경로로 부르면
        #    평범한 일반 파일도 `상대 != 절대` 라 전부 심링크로 판정된다. 묻는 것은
        #    "이 파일이 심링크인가"이므로 그대로 묻는다.
        if 기대.is_symlink():
            위반.append(f"DB 가 심링크다 — {기대} → {기대.resolve()}")

        # ⚠️ **여기가 유령을 가르는 자리다.** 새로 만들어진 빈 DB 는 `init_schema` 가
        #    돌아 **표는 다 있고 행만 없다** — 크기나 무결성으로는 정상과 구별되지 않고,
        #    감사는 빈 DB 에서 모든 등식이 성립해 전부 초록이다. 행이 있는지를 물어야 한다.
        행수 = _행수(str(기대), 명세_.핵심표, uri모드=False)
        없는표 = [t for t, n in 행수.items() if n < 0]
        빈표 = [t for t, n in 행수.items() if n == 0]
        if 없는표:
            위반.append(f"{명세_.이름} 의 표가 아니다 — 없는 표 {', '.join(없는표)}")
        elif 빈표:
            위반.append(f"비어 있는 핵심표 — {', '.join(빈표)} (새로 만들어진 유령 DB 인가)")

    # ⚠️ **급감은 경고지 위반이 아니다.** 위반으로 두면, 손상 사고 뒤 정상적인 옛 세대를
    #    복원했을 때 그 세대가 최신 백업의 절반 미만이라는 이유로 **매일의 점검이 계속
    #    실패하고 자동 수집이 따라잡을 기회 자체를 못 얻는다** — 문이 불변식 2 를 깨뜨린다.
    #    유령을 가르는 일은 위의 행수 검사가 이미 한다.
    최신 = 최신검증백업(코퍼스명, 보관)
    if 최신 and 기대.is_file() and (전 := 최신.기록.get("바이트")):
        지금 = 기대.stat().st_size
        if 지금 < 전 * 0.5:
            로그(f"⚠️ DB 가 직전 세대의 절반 미만이다 — 지금 {지금/1024**3:.2f}GiB"
                 f" · {최신.날짜} {전/1024**3:.2f}GiB."
                 " 옛 세대를 복원한 직후라면 정상이고, 아니라면 사람이 봐야 한다.")

    for v in 위반:
        로그(f"🔴 {v}")
    if not 위반:
        로그(f"🟢 {명세_.이름} {기대} · {기대.stat().st_size/1024**3:.2f}GiB")
    return len(위반)


# ── 원본 열기 ────────────────────────────────────────────────────────────────


def _원본URI(db: Path) -> str:
    """`mode=ro` 가 기본이고, 그게 안 열리는 **알려진 한 조건**에서만 평범하게 연다.

    ⚠️ **`-wal` 이 없으면 WAL DB 를 읽기 전용으로 열 수 없다.** `mode=ro` 는 그 파일을
       만들 수 없어서 `unable to open database file (14)` 로 죽는다. 그런데 수집기가
       깨끗하게 끝나면 SQLite 가 `-wal` 을 지우므로, **그 뒤에 도는 백업은 전부 실패한다.**
       옆 레포에서 이 조합으로 백업 잡이 조용히 죽어 있었다.

       폴백이 안전한 이유는 **실패 조건이 곧 안전 조건**이라는 데 있다 — `-wal` 이 없다는
       것은 마지막 커넥션이 WAL 을 전부 main 으로 밀어 넣었다는 뜻이라 그 순간 체크포인트할
       내용이 아예 없다. 그래서 `-wal` 이 **있는데도** 안 열리면 그 전제가 성립하지 않으므로
       짐작으로 열지 않고 멈춘다.
    """
    uri = f"file:{db}?mode=ro"
    try:
        sqlite3.connect(uri, uri=True).execute("SELECT 1").close()
        return uri
    except sqlite3.Error:
        pass
    # ⚠️ **`-journal` 도 함께 막는다.** `-wal` 만 보면 롤백 저널 모드에서 원본에 **hot
    #    journal** 이 남아 있는 경우를 놓친다 — 그때 `mode=ro` 는 복구를 못 해 실패하지만
    #    `-wal` 은 없으므로 폴백이 열리고, **평범한 연결이 저널 복구를 수행하며 원본을
    #    고친다.** 백업 잡이 백업 대상을 수정하는 바로 그 사고다.
    곁 = [접미 for 접미 in ("-wal", "-journal") if Path(str(db) + 접미).exists()]
    if 곁:
        raise RuntimeError(
            f"{'·'.join(곁)} 이 있는데 mode=ro 가 안 열린다 — 알려진 실패 조건이 아니다: {db}\n"
            "  폴백은 복구할 것이 없을 때만 안전한데 그 전제가 성립하지 않으므로 멈춘다."
        )
    return str(db)


def _해시(p: Path) -> str:
    """사본의 지문. **크기 비교로는 같은 크기의 손상을 영원히 못 본다** — SQLite 파일은
    페이지 단위라 내용이 바뀌어도 크기가 같은 경우가 흔하다. 실측 3.6GiB 에 2~3초다."""
    import hashlib

    h = hashlib.sha256()
    with p.open("rb") as f:
        for 덩이 in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(덩이)
    return h.hexdigest()


def _표들(uri: str, *, uri모드=True) -> tuple[str, ...]:
    """DB 안의 실제 표 이름들. **핵심표가 아니라 전부다.**

    ⚠️ **곁기록의 `행수` 는 감사가 대참사를 재는 유일한 외부 기준선이다**(`감사 A13`).
    핵심표 다섯만 세면 국회 13개 표 중 여덟이 축 밖에 남는다 — 발의자·표결집계·의안심사가
    통째로 날아가도 **비교할 값 자체가 없어 감사가 초록이다.**

    ⚠️ **문을 함께 넓히지 마라.** `핵심표` 는 "비면 백업이 아니다"를 묻는 다른 질문이고,
    아직 안 채워진 표 하나 때문에 백업이 거절되면 복구 중인 DB 가 영영 백업되지 않는다.
    """
    conn = sqlite3.connect(uri, uri=uri모드)
    try:
        return tuple(
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
                " AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        )
    except sqlite3.Error:
        return ()
    finally:
        conn.close()


def _행수(uri: str, 표: tuple[str, ...], *, uri모드=True) -> dict[str, int]:
    conn = sqlite3.connect(uri, uri=uri모드)
    try:
        나온것 = {}
        for t in 표:
            try:
                나온것[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                나온것[t] = -1   # 표가 없다. 아래 검증이 이걸 위반으로 읽는다.
        return 나온것
    finally:
        conn.close()


# ── 락 ──────────────────────────────────────────────────────────────────────


class 락:
    """수집기와 같은 `flock` 을 같은 파일에 건다. **판정을 커널에 맡긴다.**

    ⚠️ **PID 를 읽어 `os.kill(pid, 0)` 로 판정하지 마라.** 법령이 실측으로 버린 방식이다 —
       죽은 실행의 PID 를 무관한 프로세스가 물려받으면 영원히 물러나고, `exists()` 와 쓰기
       사이가 열려 있어 동시에 들어온 12개가 전부 락을 잡았다.

    ⚠️ **못 잡았다고 백업을 거르지 않는다.** `VACUUM INTO` 는 동시 쓰기와 무관하게 일관된
       사본을 주므로, 락이 사 주는 것은 "**이행 도중이 아니다**"뿐이다. 바쁜 날 백업이
       통째로 빠지는 편이 훨씬 나쁘다. 그래서 못 잡으면 뜨되 `락없음` 을 기록에 남기고,
       그 라벨을 읽어 파괴적 이행을 거절할지는 `이행()` 이 정한다.
    """

    def __init__(self, db: Path):
        self.path = Path(str(db) + ".lock")
        self.잡음 = False
        self._fd: int | None = None

    def 잡기(self, 대기초: float = 0.0) -> bool:
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        마감 = time.monotonic() + 대기초
        while True:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.잡음 = True
                return True
            except OSError as e:
                # ⚠️ **「남이 쥐고 있다」와 「락 장치가 고장 났다」는 다른 사건이다.**
                #    비차단 `flock` 이 '이미 잡혀 있다'로 주는 것은 EAGAIN 뿐이고,
                #    ENOLCK·ENOTSUP 은 락이 안 되는 파일시스템이라는 뜻이다. 그걸
                #    `락없음` 으로 적으면 **모든 세대가 락없음 라벨을 달고, 그 라벨을
                #    읽는 이행 문이 전부 거절한다** — 백업은 도는데 수집이 멈춘다.
                if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                    os.close(self._fd)
                    self._fd = None
                    raise
                if time.monotonic() >= 마감:
                    os.close(self._fd)
                    self._fd = None
                    return False
                time.sleep(min(5.0, max(0.1, 마감 - time.monotonic())))

    def 놓기(self) -> None:
        if self._fd is not None:
            os.close(self._fd)   # 닫으면 커널이 놓는다. 파일은 지우지 않는다.
            self._fd = None
            self.잡음 = False


# ── 백업 ────────────────────────────────────────────────────────────────────


def 백업(
    코퍼스명: str,
    *,
    db=None,
    보관=None,
    이행직전=False,
    감사=True,
    락대기초=300.0,
    로그=print,
) -> dict:
    """한 세대를 뜨고, 검증하고, 원자적으로 승격한다. **셋은 하나의 트랜잭션이다** —
    검증을 통과하지 못한 사본은 백업이 아니므로 사다리에 들어가지 않는다."""
    명세_ = 코퍼스[코퍼스명]
    원본 = Path(db) if db else 명세_.db경로()
    보관 = Path(보관 or os.environ.get("CORPUS_BACKUP_DIR") or 기본보관)
    스테이징 = 보관 / ".staging"

    # ── preflight ────────────────────────────────────────────────────────
    if not 원본.is_file():
        raise SystemExit(f"🔴 원본이 일반 파일이 아니다: {원본}")
    바이트 = 원본.stat().st_size
    if 바이트 == 0:
        raise SystemExit(f"🔴 원본이 비어 있다: {원본}")

    보관.mkdir(parents=True, exist_ok=True)
    스테이징.mkdir(parents=True, exist_ok=True)

    # ⚠️ **모드 비트가 아니라 실제로 써 봐야 안다.** 러너는 GUI 없는 LaunchAgent 라
    #    보호된 폴더 접근이 TCC 에서 조용히 EPERM 으로 막힐 수 있는데, 그 경우에도
    #    `os.access(W_OK)` 는 통과한다.
    시험 = 보관 / ".쓰기시험"
    try:
        시험.write_bytes(b"")
        시험.unlink()
    except OSError as e:
        raise SystemExit(f"🔴 보관 폴더에 쓸 수 없다: {보관} ({e})") from e

    # ⚠️ **같은 코퍼스의 백업 둘이 겹치면 안 된다.** Actions 의 `concurrency` 는 같은
    #    워크플로 실행끼리만 직렬화하므로 직접 실행·이행직전 백업은 막지 못한다. 겹치면
    #    한쪽이 **다른 쪽이 아직 쓰고 있는 조각을 승격**할 수 있다.
    자기락 = 락(보관 / f".{명세_.이름}.백업")
    if not 자기락.잡기(0):
        raise SystemExit(f"🔴 {명세_.이름} 백업이 이미 돌고 있다. 물러난다.")

    # 앞선 실행이 검증 실패나 SIGKILL 로 남긴 조각.
    # ⚠️ **지금 도는 조각을 지우면 안 된다.** 이름에 pid 가 붙어 있어도 시간을 안 보면,
    #    나중에 시작한 실행이 **먼저 시작한 실행의 살아 있는 조각을 unlink** 한다 —
    #    그러면 앞 실행은 이미 사라진 inode 를 계속 쓰고, 승격할 때 엉뚱한 파일을 올린다.
    낡음 = time.time() - 6 * 3600
    for 조각 in 스테이징.glob(f"{명세_.이름}-*.partial*"):
        if 조각.stat().st_mtime < 낡음:
            로그(f"⚠️ 앞선 실행이 남긴 조각을 지운다: {조각.name}")
            조각.unlink()

    # 사본 하나가 통째로 들어가야 한다. 디스크를 채우고 죽는 대신 여기서 멈춘다.
    여유 = shutil.disk_usage(스테이징).free
    필요 = 바이트 + 여유바이트
    if 여유 < 필요:
        raise SystemExit(
            f"🔴 여유 공간 부족 — 필요 {필요/1024**3:.1f}GiB · 가용 {여유/1024**3:.1f}GiB"
        )

    # ── 락 ───────────────────────────────────────────────────────────────
    L = 락(원본)
    if not L.잡기(락대기초):
        로그(f"⚠️ {락대기초:.0f}초 기다렸지만 수집 락을 못 잡았다. 뜨되 `락없음` 으로 남긴다 —"
             " VACUUM INTO 는 동시 쓰기에 안전하지만 **이행 도중이 아니라는 보장이 없다.**")

    날짜 = datetime.now(KST).strftime("%Y-%m-%d")
    꼬리 = "-이행직전" if 이행직전 else ""
    # ⚠️ **스테이징 이름에 pid 를 넣는다.** 날짜만으로 지으면 같은 코퍼스의 두 실행이
    #    같은 경로를 공유해, 한쪽이 검증을 마치고 `replace` 할 때 그 자리에 있는 것이
    #    **다른 쪽이 쓰고 있는 파일**일 수 있다. 위 자기락이 1차 방어이고 이건 2차다.
    stage = 스테이징 / f"{명세_.이름}-{날짜}{꼬리}.{os.getpid()}.partial"

    def 치우기():
        # `-journal` 까지 지운다. VACUUM INTO 산출물은 롤백 저널 모드라 중단되면
        # `-wal` 이 아니라 `-journal` 이 남는다.
        for 접미 in ("", "-journal", "-wal", "-shm"):
            Path(str(stage) + 접미).unlink(missing_ok=True)

    기록: dict = {
        "코퍼스": 코퍼스명,
        "날짜": 날짜,
        "이행직전": 이행직전,
        "원본": str(원본),
        "락잡음": L.잡음,
        "생성시각": datetime.now(KST).isoformat(),
    }
    try:
        치우기()
        src = _원본URI(원본)
        uri모드 = src.startswith("file:")
        셀표 = tuple(dict.fromkeys(명세_.핵심표 + _표들(src, uri모드=uri모드)))
        전행수 = _행수(src, 셀표, uri모드=uri모드)

        시작 = time.monotonic()
        conn = sqlite3.connect(src, uri=uri모드)
        try:
            conn.execute("VACUUM INTO ?", (str(stage),))
        finally:
            conn.close()
        기록["소요초"] = round(time.monotonic() - 시작, 1)
        기록["바이트"] = stage.stat().st_size
        후행수 = _행수(src, 셀표, uri모드=uri모드)

        # ⚠️ **여기서 수집 락을 놓는다.** 사본이 다 떠졌으므로 아래 검증과 감사는 원본을
        #    전혀 안 본다. 감사까지 붙들고 있으면 최대 30분을 쥐게 되는데, 예약이 밀려
        #    04:00·06:00 수집과 겹치면 **수집기가 락을 못 잡고 물러나며 종료코드 0 을 낸다** —
        #    수집하지 않은 날이 초록으로 보인다. 백업이 수집을 굶기면 안 된다.
        L.놓기()

        # ── 사본 검증 ────────────────────────────────────────────────────
        # 사본은 아무도 쓰지 않는 정적 파일이라 `immutable=1` 로 연다 — 잠금과 저널 복구를
        # 통째로 건너뛰므로 **검증하다가 백업 옆에 `-shm`·`-wal` 잔재를 흘리지 않는다.**
        사본URI = f"file:{stage}?immutable=1"
        c = sqlite3.connect(사본URI, uri=True)
        try:
            기록["quick_check"] = c.execute("PRAGMA quick_check(1)").fetchone()[0]
            기록["fk위반"] = len(c.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            c.close()

        사본행수 = _행수(사본URI, 셀표)
        기록["행수"] = 사본행수

        위반 = []
        if 기록["quick_check"] != "ok":
            위반.append(f"quick_check {기록['quick_check']}")
        if 기록["fk위반"]:
            위반.append(f"FK 위반 {기록['fk위반']}행")
        for t, n in 사본행수.items():
            # ⚠️ **하한은 VACUUM 전후의 작은 쪽이다.** 사본은 그 사이 어느 시점의 스냅숏이라
            #    앞의 수만 보면, 그동안 정상적으로 행을 지운 정리·이행이 **온전한 사본을
            #    거절하게** 만든다. 뒤의 수만 보면 그 사이 늘어난 만큼을 놓친다.
            하한 = min(전행수.get(t, -1), 후행수.get(t, -1))
            # ⚠️ **거절은 핵심표에서만 한다.** 나머지 표는 기준선을 만들려고 세는 것이지
            #    문이 아니다 — 아직 안 채워진 표 하나로 백업을 거절하면 복구 중인 DB 가
            #    영영 백업되지 않고, 그건 문이 불변식 2 를 깨뜨리는 것이다.
            if t not in 명세_.핵심표:
                continue
            if n < 0:
                위반.append(f"{t} 표가 사본에 없다")
            elif n == 0:
                위반.append(f"{t} 0행")
            elif 하한 >= 0 and n < 하한:
                위반.append(f"{t} 사본 {n:,} < 원본 {하한:,}")
        if 위반:
            기록["검증"] = False
            기록["위반"] = 위반
            raise SystemExit("🔴 사본이 검증을 통과하지 못했다 — 승격하지 않는다:\n  "
                             + "\n  ".join(위반))
        기록["검증"] = True

        # ── 감사 ─────────────────────────────────────────────────────────
        # 라벨이지 문이 아니다. 파일 머리말의 이유를 참조.
        if 감사:
            기록 |= _감사(명세_, stage, 로그)

        # ⚠️ **지문은 감사 뒤에 뜨고, 뜬 뒤에 파일을 잠근다. 순서가 전부다.**
        #    감사는 별도 프로세스가 `--db` 로 이 파일을 여는데, 코퍼스의 `connect()` 는
        #    열면서 `journal_mode=WAL` 을 걸어 **DB 헤더를 고친다** — 읽기만 하는 감사가
        #    사본을 바꾸는 것이다. 지문을 그 앞에서 뜨면 방금 뜬 사본이 자기 곁기록과
        #    갈린 채로 승격되고, 그 지문을 다시 재는 `이행전_확인` 이 **영원히 거절한다.**
        #    백업을 다시 떠도 열리지 않는 문이라 파괴적 이행이 통째로 막힌다
        #    (실측 2026-08-28: LAW 두 세대 모두 헤더가 `0202`=WAL 로 뒤집혀 있었고
        #    `migrate()` 가 "사본 지문이 뜰 때와 다르다"로 거부했다).
        #
        #    ⚠️ **앞당겨서 잠그면 안 된다** — 사본이 읽기 전용이면 그 `journal_mode=WAL`
        #    이 실패해 **감사가 통째로 죽고 라벨이 영영 '못 셌다'** 가 된다. 잠그는 것은
        #    승격 뒤의 변질을 막는 것이지 감사를 막는 것이 아니다.
        기록["sha256"] = _해시(stage)
        stage.chmod(0o444)

        최종 = _승격자리(보관, 명세_.이름, 날짜, 꼬리, 기록, 로그)
        기록["경로"] = str(최종)

        # ⚠️ **곁기록을 먼저 치우고 승격한다.** DB 를 먼저 바꾸면 그 직후 죽었을 때
        #    **새 사본 옆에 옛 판정이 남는다** — 감사가 빨간 사본이 「통과」로 읽힌다.
        #    이 순서면 그 창에서 남는 것은 '곁기록 없는 사본' = 미검증이라, 사고가
        #    안전한 쪽으로 넘어진다.
        곁 = 최종.with_suffix(".json")
        곁.unlink(missing_ok=True)
        stage.replace(최종)
        곁.write_text(json.dumps(기록, ensure_ascii=False, indent=1))
    finally:
        치우기()
        L.놓기()
        자기락.놓기()

    로그(f"🟢 {최종.name} · {기록['바이트']/1024**3:.2f}GiB · {기록['소요초']}초"
         f" · 감사 {기록.get('감사요약', '건너뜀')}"
         + ("" if 기록["락잡음"] else " · ⚠️ 락없음"))
    return 기록


def _승격자리(보관: Path, 이름: str, 날짜: str, 꼬리: str, 기록: dict, 로그) -> Path:
    """어느 파일명으로 올릴지 정한다. **성한 사본을 덮어쓰지 않는 것이 유일한 규칙이다.**

    ⚠️ 날짜만으로 이름을 지으면 같은 날 두 번째 실행이 첫 번째를 무조건 덮는다. 02:40 에
       감사까지 통과한 사본을 떠 놓고, 그날 수집이 데이터를 망가뜨린 뒤 백업을 한 번 더
       돌리면 — **가장 필요한 그 사본이 바로 그 순간 사라진다.** 구조 검증만 통과하면
       승격되기 때문이다. `정리()` 의 "마지막 성한 사본을 남긴다"는 이미 파일이 덮인
       뒤라 아무 도움이 안 된다.

    그래서 **더 나쁜 것이 더 나은 것을 밀어내지 않는다**로 규칙을 세운다. 같은 날짜의
    기존 세대가 감사를 통과했는데 이번 것이 아니면, 이번 것을 `#N` 으로 따로 올린다.
    둘 다 남으므로 아무것도 잃지 않고, 어느 쪽이 성한지는 곁기록이 말한다.
    """
    자리 = 보관 / f"{이름}-{날짜}{꼬리}.db"
    곁 = 자리.with_suffix(".json")
    if not 자리.exists():
        return 자리
    기존 = {}
    if 곁.is_file():
        try:
            기존 = json.loads(곁.read_text())
        except ValueError:
            pass
    if 기존.get("감사통과") is True and 기록.get("감사통과") is not True:
        for n in range(2, 100):
            대안 = 보관 / f"{이름}-{날짜}{꼬리}#{n}.db"
            if not 대안.exists():
                로그(f"⚠️ {자리.name} 은 감사를 통과한 사본이라 덮지 않는다."
                     f" 이번 것은 {대안.name} 으로 올린다.")
                return 대안
    return 자리


def _감사(명세_: 명세, 사본: Path, 로그) -> dict:
    """코퍼스 자신의 감사를 **사본에 대고** 돌린다. `--db` 가 그 계약이다.

    ⚠️ **결과를 승격의 문으로 쓰지 마라.** 파일 머리말의 이유 — 감사가 빨간 날이 백업이
       가장 필요한 날이다. 여기서 하는 일은 세대에 정직한 라벨을 붙이는 것뿐이고,
       그 라벨로 무엇을 거절할지는 읽는 쪽이 정한다.

    별도 프로세스인 이유는 감사가 각자 `uv run` 스크립트라 의존성을 스스로 선언하기
    때문이다. 임포트로 끌어오면 그 선언이 무의미해지고, 감사가 죽을 때 백업까지 죽는다.
    """
    try:
        r = subprocess.run(
            ["uv", "run", 명세_.감사스크립트, "--db", str(사본)],
            cwd=스킬, capture_output=True, text=True, timeout=1800,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        로그(f"⚠️ 감사를 못 돌렸다: {e}")
        return {"감사위반": None, "감사요약": f"못 돌림 ({type(e).__name__})"}

    # ⚠️ **판정은 종료코드가 진다. 세어 본 수는 장식이다.** 표를 파싱해 0 이 나왔다는 것은
    #    "위반이 없다"가 아니라 "이 형식에서는 못 셌다"일 수 있고, 그 둘을 같게 두면
    #    감사가 빨간 세대에 「통과」 라벨이 붙는다 — 이 레포가 도처에서 당하고 있는,
    #    빈 결과를 0 으로 읽어 초록을 내는 바로 그 함정이다.
    빨강 = [l.rstrip("🔴 ").strip() for l in r.stdout.splitlines() if l.rstrip().endswith("🔴")]
    죽음 = r.returncode not in (0, 1)
    return {
        "감사통과": r.returncode == 0,
        "감사위반": None if 죽음 else len(빨강),
        "감사요약": (f"감사가 죽었다 (종료코드 {r.returncode})" if 죽음
                     else "통과" if r.returncode == 0
                     else f"위반 {len(빨강)}건" if 빨강
                     else "위반 (게이트를 못 셌다)"),
        # 어느 게이트가 빨간지까지 남긴다 — 사흘 뒤에 "언제부터 이상해졌나"에 답하는 자리다.
        "감사빨강": 빨강,
    }


# ── 정리 ────────────────────────────────────────────────────────────────────


def 정리(코퍼스명: str, 보관=None, 로그=print) -> dict:
    """사다리 밖으로 나간 세대를 지운다. **지운 것은 전부 말한다 — 조용한 삭제는 없다.**

    남기는 규칙: 최근 7일 ∪ 매월 1일자 최신 12개 ∪ **가장 최근의 감사 통과본** ∪
    90일 안의 이행 직전본.

    ⚠️ **감사 통과본을 무조건 남기는 항이 핵심이다.** 날짜 사다리만 두면, 손상이 며칠
       이어질 때 손상된 세대가 매일 하루치 자리를 차지해 **마지막 성한 사본을 밀어낸다.**
       사다리는 "언제"를 보관하지 "쓸 만한가"를 보지 않아서 생기는 구멍이다.
    """
    나온것 = 세대들(코퍼스[코퍼스명], 보관)
    if not 나온것:
        return {"남김": [], "지움": []}

    보통 = [g for g in 나온것 if not g.이행직전]

    # ⚠️ **사다리는 '날짜'로 센다, '파일'로 세지 않는다.** 같은 날 두 세대가 있을 수 있어서
    #    (`_승격자리` 의 `#N`) 파일로 세면 하루가 사다리 두 칸을 먹고 **보관 기간이 조용히
    #    절반으로 줄어든다.**
    날짜들 = sorted({g.날짜 for g in 보통}, reverse=True)
    산다 = set(날짜들[:보관일]) | set([d for d in 날짜들 if d.endswith("-01")][:보관월])
    남길 = {g.경로 for g in 보통 if g.날짜 in 산다}

    if 성한것 := _쓸만한_최신본(보통, 로그):
        남길.add(성한것.경로)

    한계 = (datetime.now(KST) - timedelta(days=이행직전보존일)).strftime("%Y-%m-%d")
    남길 |= {g.경로 for g in 나온것 if g.이행직전 and g.날짜 >= 한계}

    남김, 지움 = [], []
    for g in 나온것:
        if g.경로 in 남길:
            남김.append(g)
            continue
        바이트 = g.경로.stat().st_size
        g.경로.unlink()
        g.곁.unlink(missing_ok=True)
        지움.append(g)
        로그(f"보관 기간이 지난 사본을 지웠다: {g.경로.name} ({바이트/1024**3:.2f}GiB)")
    return {"남김": 남김, "지움": 지움}


def _쓸만한_최신본(세대들_: list[세대], 로그) -> 세대 | None:
    """감사를 통과했다고 **적혀 있고** 실제로 열리기도 하는 가장 최근 세대.

    ⚠️ **곁기록만 믿으면 안 된다.** 저장장치에서 썩은 파일의 곁기록에도 `감사통과: true`
       는 그대로 남아 있다. 그걸 "마지막 성한 사본"으로 골라 놓고 진짜 성한 옛 사본을
       지우면, 다음 복원 검증에서 손상을 발견해도 돌아갈 곳이 이미 없다.

    전수 `integrity_check` 는 3.6GiB 에 너무 비싸므로 여기서는 **열려서 핵심 표를 읽는지**
    까지만 본다 — 잘림·헤더 손상 같은 흔한 사고를 잡는다. 깊은 검사는 주 1회 복원 검증이 진다.
    """
    for g in 세대들_:
        if not g.감사통과:
            continue
        try:
            c = sqlite3.connect(f"file:{g.경로}?immutable=1", uri=True)
            try:
                c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()
            finally:
                c.close()
            return g
        except sqlite3.Error as e:
            로그(f"⚠️ {g.경로.name} 은 감사 통과로 적혀 있지만 열리지 않는다 ({e}) —"
                 " 마지막 성한 사본으로 치지 않는다.")
    return None


def 반출(코퍼스명: str, 보관=None, 반출루트=None, 로그=print) -> list[Path]:
    """최신 세대만 iCloud 로 민다. **사다리 전체는 애초에 안 들어간다** — 한 세대 4.3GiB,
    iCloud 잔여 24.2GiB(실측 2026-08-28).

    ⚠️ **업로드는 비동기라 성공 조건으로 쓸 수 없다.** `mv` 가 끝나도 클라우드 도착은
       보장되지 않는다. 잡을 붙잡아 기다리면 러너가 점유되어 수집이 밀리므로 기다리지
       않고, 대신 `--상태` 가 "로컬에서 비워짐"(= 업로드 확증)을 **양의 신호로만** 낸다.
       비워지지 않았다는 것이 미업로드라는 뜻은 아니다.
    """
    대상 = os.environ.get("CORPUS_BACKUP_MIRROR")
    if 반출루트 is None:
        # 빈 문자열은 "반출하지 마라"라는 명시적 지시다. **인자로 준 경로는 이걸 이긴다** —
        # 환경이 정한 기본값이 인자를 덮으면 인자가 있으나 마나가 된다.
        if 대상 == "":
            return []
        반출루트 = 대상 or 기본반출
    밖 = Path(반출루트)
    나온것 = [g for g in 세대들(코퍼스[코퍼스명], 보관) if not g.이행직전][:반출세대]
    if not 나온것:
        return []
    try:
        밖.mkdir(parents=True, exist_ok=True)
        시험 = 밖 / ".쓰기시험"
        시험.write_bytes(b"")
        시험.unlink()
    except OSError as e:
        로그(f"⚠️ 반출 폴더에 쓸 수 없어 건너뛴다: {밖} ({e})")
        return []

    보낸것, 성한반출 = [], []
    for g in 나온것:
        목적 = 밖 / g.경로.name
        # ⚠️ **크기로 같은지 판정하지 마라.** SQLite 는 페이지 단위라 내용이 바뀌어도 크기가
        #    같은 경우가 흔하다. 크기만 보면 **같은 크기로 손상된 반출본이 영원히 안 고쳐진다.**
        if 목적.exists() and (밖 / g.곁.name).is_file():
            try:
                if json.loads((밖 / g.곁.name).read_text()).get("sha256") == g.기록.get("sha256") \
                        and 목적.stat().st_size == g.경로.stat().st_size:
                    성한반출 += [목적.name, g.곁.name]
                    continue
            except (ValueError, OSError):
                pass
        # 같은 볼륨 안에서 스테이징한 뒤 rename 한다. 백업 폴더에 직접 쓰면 iCloud 가
        # 반쯤 쓰인 4GiB 를 즉시 올리기 시작한다.
        임시 = 밖 / (g.경로.name + ".partial")
        try:
            shutil.copyfile(g.경로, 임시)
            임시.replace(목적)
            shutil.copyfile(g.곁, 밖 / g.곁.name)
            보낸것.append(목적)
            성한반출 += [목적.name, g.곁.name]
            로그(f"반출: {목적.name}")
        except OSError as e:
            임시.unlink(missing_ok=True)
            로그(f"⚠️ 반출 실패 {g.경로.name}: {e}")

    # ⚠️ **회수는 이번에 성한 반출본이 실제로 있을 때만 한다.** 종전에는 '보낼 예정이던
    #    이름'으로 유지 집합을 만들어서, iCloud 용량 부족이나 I/O 오류로 복사가 **전부
    #    실패해도** 옛 반출본을 지웠다 — 반출본이 0개가 되고, 그 사실이 종료코드에도
    #    안 실려 워크플로와 워치독은 초록이었다.
    if not 성한반출:
        로그("⚠️ 이번에 성한 반출본이 하나도 없다 — 옛 반출본을 지우지 않는다.")
        return 보낸것

    이름 = 코퍼스[코퍼스명].이름
    for p in sorted(밖.iterdir()):
        m = 세대이름.match(p.name.removesuffix(".json"))
        if m and m["이름"] == 이름 and p.name not in 성한반출:
            p.unlink()
            로그(f"반출본 회수: {p.name}")
    return 보낸것


# ── 복원 검증 ───────────────────────────────────────────────────────────────


def 복원검증(코퍼스명: str, 날짜=None, 보관=None, 로그=print) -> dict:
    """한 세대를 **임시 경로로 실제 복원해** `integrity_check` 와 코퍼스 감사를 돌린다.

    ⚠️ **「업로드됨」은 백업이 아니다.** 백업이라고 부를 수 있는 유일한 근거는 **다시 열어
       감사가 도는 것**이고, 그건 열어 보기 전까지 아무도 모른다. `quick_check` 는 페이지
       단위 검사라 뜨는 순간에는 통과했어도 그 뒤 저장 계층에서 썩은 것을 못 본다 —
       그래서 여기서는 전수인 `integrity_check` 를 쓴다.
    """
    나온것 = 세대들(코퍼스[코퍼스명], 보관)
    g = next((x for x in 나온것 if x.날짜 == 날짜), None) if 날짜 else next(iter(나온것), None)
    if not g:
        raise SystemExit(f"🔴 복원할 세대가 없다 ({코퍼스명}{' ' + 날짜 if 날짜 else ''})")

    import tempfile

    with tempfile.TemporaryDirectory(prefix="복원검증-") as d:
        사본 = Path(d) / g.경로.name
        로그(f"{g.경로.name} → {사본}")
        shutil.copyfile(g.경로, 사본)

        # ⚠️ **먼저 지문을 대조한다.** `integrity_check` 는 SQLite 구조만 보므로, 뜬 뒤에
        #    바이트가 바뀌어도 구조가 성하면 통과한다. 지문이 갈리면 그 자체가 사건이다.
        지문 = _해시(사본)
        기대지문 = g.기록.get("sha256")
        결과 = {"날짜": g.날짜, "sha256일치": None if not 기대지문 else 지문 == 기대지문}
        if 결과["sha256일치"] is False:
            로그(f"🔴 지문이 뜰 때와 다르다 — 기록 {기대지문[:12]}… · 지금 {지문[:12]}…")
        elif 기대지문:
            로그(f"지문 일치 {지문[:12]}…")

        c = sqlite3.connect(str(사본))
        try:
            무결 = c.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            c.close()
        로그(f"integrity_check: {무결}")
        결과["integrity_check"] = 무결
        결과 |= _감사(코퍼스[코퍼스명], 사본, 로그)
        결과 |= _반출본_상태(코퍼스명, g, 로그)

    ok = (무결 == "ok" and 결과.get("감사통과") is True
          and 결과["sha256일치"] is not False)
    로그(("🟢 복원 검증 통과" if ok else "🔴 복원 검증 실패") + f" — {결과.get('감사요약')}")
    결과["통과"] = ok
    return 결과


def _반출본_상태(코퍼스명: str, g: 세대, 로그) -> dict:
    """반출본이 이 세대와 같은 것인지 **말한다.** 판정에는 넣지 않는다.

    ⚠️ 반출본을 열어 보지 않는 이유는 iCloud 가 로컬에서 비운 파일을 **여는 순간 통째로
       내려받기** 때문이다 — 주 1회 4GiB 를 끌어오는 비용이 얻는 것보다 크다. 대신 곁기록의
       지문을 대조해 "같은 세대가 저기 있다"까지 확인하고, 없거나 다르면 그 사실을 낸다.
    """
    대상 = os.environ.get("CORPUS_BACKUP_MIRROR")
    if 대상 == "":
        return {"반출본": "반출 안 함"}
    밖 = Path(대상 or 기본반출)
    저쪽 = 밖 / g.경로.name
    저쪽곁 = 밖 / g.곁.name
    if not 저쪽.exists():
        로그(f"⚠️ 반출본이 없다: {저쪽}")
        return {"반출본": "없음"}
    try:
        같나 = json.loads(저쪽곁.read_text()).get("sha256") == g.기록.get("sha256")
    except (ValueError, OSError):
        같나 = False
    로그(f"반출본 {'일치' if 같나 else '⚠️ 곁기록이 다르다'}: {저쪽}")
    return {"반출본": "일치" if 같나 else "불일치"}


# ── CLI ─────────────────────────────────────────────────────────────────────


def _상태(코퍼스명들: list[str], 보관=None) -> int:
    나쁨 = 0
    for 이름 in 코퍼스명들:
        나온것 = 세대들(코퍼스[이름], 보관)
        최신 = 최신검증백업(이름, 보관)
        신선 = 신선한가(최신)
        print(f"\n── {코퍼스[이름].이름} ── {len(나온것)}세대 ·"
              f" 합계 {sum(g.경로.stat().st_size for g in 나온것)/1024**3:.1f}GiB")
        if not 최신:
            print("  🔴 검증된 세대가 하나도 없다")
            나쁨 += 1
        elif not 신선:
            print(f"  🔴 가장 최근 검증본이 24시간보다 오래됐다 ({최신.날짜})")
            나쁨 += 1
        for g in 나온것:
            st = g.경로.stat()
            print(f"  {g.경로.name:<34} {st.st_size/1024**3:>6.2f}GiB"
                  f"  감사 {g.기록.get('감사요약', '?'):<10}"
                  f"  {'락없음 ' if not g.기록.get('락잡음') else ''}"
                  f"{'로컬에서 비워짐' if st.st_blocks == 0 else ''}")
    return 1 if 나쁨 else 0


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("코퍼스", nargs="*", choices=list(코퍼스), default=None,
                    help="기본: 전부")
    ap.add_argument("--보관", help=f"세대 사다리를 둘 곳 (기본 $CORPUS_BACKUP_DIR 또는 {기본보관})")
    ap.add_argument("--db", help="원본을 명시한다. 코퍼스를 하나만 지정할 때만 쓸 수 있다")
    ap.add_argument("--이행직전", action="store_true",
                    help=f"파괴적 이행 직전본으로 표시한다 — 사다리와 무관하게 {이행직전보존일}일 붙잡는다")
    ap.add_argument("--감사-생략", action="store_true", dest="감사생략",
                    help="사본에 코퍼스 감사를 돌리지 않는다 (라벨이 '건너뜀'이 된다)")
    ap.add_argument("--반출-생략", action="store_true", dest="반출생략")
    ap.add_argument("--락대기", type=float, default=300.0, metavar="초",
                    help="수집 락을 이만큼 기다린다. 못 잡아도 뜨되 `락없음` 으로 남긴다")
    ap.add_argument("--상태", action="store_true",
                    help="세대 목록과 신선도만 낸다. 24시간 넘었으면 비영으로 끝난다")
    ap.add_argument("--점검", action="store_true",
                    help="수집 전에 원본이 우리가 아는 그 파일인지 본다 (유령 DB 방지)")
    ap.add_argument("--복원검증", nargs="?", const="", metavar="YYYY-MM-DD",
                    help="세대를 임시 경로로 복원해 integrity_check 와 감사를 돌린다 (기본: 최신)")
    a = ap.parse_args(argv)

    대상 = a.코퍼스 or list(코퍼스)
    if a.db and len(대상) != 1:
        ap.error("--db 는 코퍼스를 하나만 지정할 때만 쓸 수 있다")

    if a.상태:
        return _상태(대상, a.보관)

    if a.점검:
        return 1 if sum(점검(이름, db=a.db, 보관=a.보관) for 이름 in 대상) else 0

    if a.복원검증 is not None:
        나쁨 = 0
        for 이름 in 대상:
            print(f"\n── {코퍼스[이름].이름} 복원 검증 ──")
            나쁨 += not 복원검증(이름, a.복원검증 or None, a.보관)["통과"]
        return 1 if 나쁨 else 0

    나쁨 = 0
    for 이름 in 대상:
        print(f"\n── {코퍼스[이름].이름} ──")
        try:
            백업(이름, db=a.db, 보관=a.보관, 이행직전=a.이행직전,
                 감사=not a.감사생략, 락대기초=a.락대기)
        except (SystemExit, Exception) as e:
            # ⚠️ **한 코퍼스가 실패해도 나머지는 뜬다.** `SystemExit` 만 잡으면 안 된다 —
            #    경로가 사라지거나 `mode=ro` 전제가 깨지면 평범한 예외가 나오고, 그러면
            #    법령이 넘어진 날 국회 사본까지 **함께** 잃는다. 둘은 독립된 사건이다.
            print(f"🔴 {코퍼스[이름].이름} 백업 실패: {e}", file=sys.stderr)
            나쁨 += 1
            continue
        정리(이름, a.보관)
        if not a.반출생략:
            # ⚠️ 반출은 비동기 업로드라 **도착**을 성공 조건으로 쓸 수 없다. 하지만 복사
            #    자체가 하나도 안 된 것은 다른 사건이다 — 그건 여기서 빨갛게 낸다.
            if not 반출(이름, a.보관) and 세대들(코퍼스[이름], a.보관):
                if os.environ.get("CORPUS_BACKUP_MIRROR") != "":
                    print(f"⚠️ {코퍼스[이름].이름} 반출본을 새로 보내지 못했다"
                          " (이미 같은 것이 있으면 정상이다)", file=sys.stderr)
    return 1 if 나쁨 else 0


if __name__ == "__main__":
    raise SystemExit(_main())
