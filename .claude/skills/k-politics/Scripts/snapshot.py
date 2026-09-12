#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""수집기의 원천 DB 둘과 소관기관 DB 를 **읽기 전용 통합 스냅샷** 하나로 접는다.

## 왜 스냅샷인가

수집기는 라이브 파일에 제자리로 쓴다. 그 파일을 조회하는 쪽이 직접 열면 세 가지가 따라온다 —
WAL 체크포인트 타이밍에 따라 답이 흔들리고, 실수로 쓰는 경로가 열려 있고, 두 DB 를 가로지르는
질의를 매번 ATTACH 로 조립해야 한다(SQLite 뷰는 ATTACH 한 DB 의 객체를 참조하지 못한다).
스냅샷은 셋을 한 번에 없앤다: 하루 한 판본으로 고정되고, `immutable=1` 로 열려 쓰기가 불가능하며,
두 코퍼스가 한 파일에 있어 교차 뷰가 성립한다.

## 스키마의 진실 원천은 어디인가

**원천 표·뷰·인덱스의 DDL 은 원천 `sqlite_master` 에서 그대로 복사한다.** 손으로 베껴 두면
수집기가 컬럼을 늘린 날 스냅샷이 조용히 그 컬럼을 잃는다 — 표별 행 수 대조는 이걸 못 잡는다.

바꾸는 것은 **주석뿐**이고, 그 목록이 아래 `주석패치` 다. 원천 주석은 수집기 관리자를 독자로
쓰인 자리가 남아 있다("열린 값 집합의 분포는 audit R1") — 이 스냅샷의 독자는 의원실 일을 하는
클로드라 그 좌표는 "그러면 무엇을 하라"로 바꾼다. 패치의 대상 문자열이 원천에서 사라지면
빌드가 실패한다. 그게 드리프트의 알림이다 — 조용히 넘어가면 우리는 이미 없는 주석을 고치고 있다고
믿게 된다.

접는 표(`수집상태`·`메타`)와 새로 세우는 표·뷰의 DDL 만 이 파일의 `SCHEMA` 상수가 진다.
`소관기관` 은 자기 DB 가 진실 원천이라 원천 셋과 같은 방식으로 DDL 째 복사한다.
매일 새로 만드니 이행(migration)이 없고, 그래서 드리프트도 없다.

## 실측 (2026-09-12)

- 원천: 국회 0.65GB(의안 21,271 · 발언 136만), 법령 4.0GB(법령 6,499 · 조문 32만 · 위임 189만)
- `발언.내용`·`조문.전문` 전 기간 LIKE 스캔이 각각 1.7초라 FTS5 는 두지 않았다. 5초를 넘으면 다시 연다.
- 법률안 20,723건 중 93.1% 가 현행·시행예정 법률에 연결된다. 미매칭 1,440건은 대부분 제정안이다.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


# ── 무엇을 옮기고 무엇을 접는가 ──────────────────────────────────────────────

# 원천에서 그대로 오는 표. 뷰·인덱스는 이 목록에 딸린 것을 자동으로 따라온다.
국회표 = ("위원회", "의원", "의원위원회", "의안", "의안심사", "발의자",
          "표결집계", "표결", "회의", "발언", "회의의안")
법령표 = ("법령", "조문", "조문요소", "부칙", "위임", "판례", "헌재결정례",
          "행정심판례", "법령해석례", "행정규칙", "행정규칙조문", "의율조문",
          "인용판례", "파싱실패", "정리후보")

# 옮기지 않는 표. `수집상태`·`메타` 는 수집기의 운영 원장이라 `신선도` 한 표로 접고,
# 두 `수집실패` 는 같은 뜻(커버리지의 구멍)이라 컬럼만 맞춰 한 표로 합친다.
# ⚠️ `수집실패` 를 접는 대상에 넣는 이유는 **이름이 양쪽에 있어서**다 — 그대로 두면 충돌한다.
접는표 = {"수집상태", "메타", "수집실패"}


# ── 주석 패치 ───────────────────────────────────────────────────────────────
#
# (코퍼스, 찾을 문자열, 바꿀 문자열). 찾을 문자열이 그 코퍼스의 DDL 전체에서 **정확히 한 번**
# 나와야 한다 — 0번이면 원천이 바뀐 것이고(빌드 실패), 여러 번이면 의도한 자리를 못 짚은 것이다.
#
# 남기는 것과 지우는 것의 기준: 이 주석이 **의원실 질의를 바꾸는가.** `audit R7` 은 그 게이트를
# 아는 사람에게만 뜻이 있으니 "무엇을 하라"로 바꾸고, "동명이인이 실재해 조인 키가 아니다" 는
# 그대로 둔다.
주석패치: tuple[tuple[str, str, str], ...] = (
    # ── 국회
    ("국회",
     "-- 열린 값 집합의 분포는 audit R1.",
     "-- 어떤 값이 오는지는 SELECT DISTINCT 처리결과 로 본다 — 열린 집합이라 목록을 외워 걸면 샌다."),
    ("국회",
     "원안 경유는 의안회의, 의안에 없는 번호는 audit R7.",
     "원안 경유는 의안회의."),
    ("국회",
     "의안번호    TEXT NOT NULL,  -- 의안에 없는 번호가 포함될 수 있다 — audit R7.",
     "의안번호    TEXT NOT NULL,  -- ⚠️ 의안 표에 없는 번호가 섞여 있어 INNER JOIN 이 회의를 조용히 뺀다."),
    ("국회",
     "상위 없이 등록된 '…소위' 별칭도 있다(audit R18).",
     "상위 없이 등록된 '…소위' 별칭도 있다."),
    ("국회",
     "현직의 확인 지연은 audit R17.",
     "현직인데도 확인일이 오래됐으면 정당이 옛 값일 수 있다."),
    ("국회",
     "⚠️ 자기 번호의 회의 연결이 없는 것으로 관측된 위원장 대안은 원안경유로 연결한다 — 회의의안의 미해소 번호는 audit R7.",
     "⚠️ 자기 번호의 회의 연결이 없는 것으로 관측된 위원장 대안은 원안경유로 연결한다."),
    ("국회",
     "의원 이름과 같아도 코드가 NULL인 행이 있어 NULL을 모두 비의원으로 세면 의원 발언이 빠진다 — 수집실패의 '발언의원'.",
     "의원 이름과 같아도 코드가 NULL인 행이 있어 NULL을 모두 비의원으로 세면 의원 발언이 빠진다 — 그 목록은 수집실패의 대상종류='발언의원'."),
    ("국회",
     "-- ⚠️ NULL = 원천이 CHECK 밖 값을 준 자리 — 수집실패의 '막힘'.",
     "-- ⚠️ NULL = 원천이 CHECK 밖 값을 준 자리 — 그 목록은 수집실패의 실패종류='막힘'."),
    # ── 법령
    ("법령",
     "날짜는 'YYYY-MM-DD'다. 저장 형식은 감사 A19·R21, 현행 판본 중복은 R25·A11이 살피며 사용자의 날짜 조건 오류는 검출하지 않는다.",
     "날짜는 'YYYY-MM-DD'다."),
    ("법령",
     "⚠ 한 법령ID에 현행 판본이 여럿일 수 있어 법령일련번호로 구별하고 부칙·원천 판본을 확인한다. 원천 중복 분류와 정리 유예는 가능한 원인이며 모든 중복의 원인으로 확정하지 않는다.",
     "⚠ 한 법령ID에 현행 판본이 여럿일 수 있어 법령일련번호로 구별한다 — 판본을 안 보고 조문을 세면 같은 조가 판본 수만큼 곱해진다. 어느 판본이 맞는지는 부칙으로 확인한다."),
    ("법령",
     "이 표는 조문에서 대상 규범으로 향하는 관계다. 위임구분 분포는 감사 R14에 있다.",
     "이 표는 조문에서 대상 규범으로 향하는 관계다. 위임구분에 어떤 값이 오는지는 SELECT DISTINCT 위임구분."),
    ("법령",
     "NULL은 아직 미해결 또는 원천 부재이며 `수집실패`의 위임행정규칙 기록이 단서다.",
     "NULL은 아직 미해결 또는 원천 부재이며 수집실패의 대상종류='위임행정규칙' 기록이 단서다."),
)


# ── 접는 표와 새로 세우는 표·뷰 ─────────────────────────────────────────────

SCHEMA = r"""
CREATE TABLE 신선도 (
  -- **이 답은 언제 기준인가.** 코퍼스 하나가 한 행이다.
  -- 사용자가 "지금 어떻게 되어 있나"를 물을 때 그 '지금'이 며칠 전인지 모르면 낡은 시점의 답을 오늘 것인 양 내놓게 된다. 그래서 시점을 묻는 질의는 여기 한 표에서 끝난다.
  코퍼스       TEXT PRIMARY KEY,  -- '국회' | '법령' | '소관기관'
  적재기준시각 TEXT,     -- 원천이 마지막으로 자료를 받은 시각(KST). NULL = 원천에 기록이 없다.
  감사통과     INTEGER,  -- 1 = 마지막 감사가 게이트를 다 통과. 0 = 위반 있음(감사상세를 본다). NULL = 감사 기록 없음.
  감사상세     TEXT,     -- 위반한 게이트와 건수. NULL = 위반 없음 또는 기록 없음.
  경고         TEXT,     -- ⚠️ 이 코퍼스의 **어느 부분이 옛 값인가**를 문장으로. NULL = 그런 부분 없음.
  빌드시각     TEXT NOT NULL  -- 이 스냅샷 파일을 만든 시각(KST). 적재기준시각과 다르다 — 빌드는 매일 돌지만 원천 수집은 실패할 수 있어, 둘이 벌어져 있으면 그만큼 수집이 멈춰 있었다는 뜻이다.
);

CREATE TABLE 수집실패 (
  -- **받으려다 못 받은 자료** 하나가 한 행이다. 남은 행만큼 그 종류의 자료에 구멍이 있다.
  -- ⚠️ "DB 에 없다"의 두 이유를 여기서 가른다 — 여기 있으면 받으려다 실패한 것이고, 여기에도 없으면 애초에 수집 범위 밖이다. **어느 쪽이든 원천에는 있을 수 있다.** 없다고 답하기 전에 ultra-search 로 원천을 확인한다.
  코퍼스     TEXT NOT NULL,  -- '국회' | '법령'
  대상종류   TEXT NOT NULL,  -- 국회: 의안상세·의안요약·회의본문·발언의원 등. 법령: 법령·판례·위임행정규칙 등. 어떤 값이 있는지는 SELECT DISTINCT 코퍼스, 대상종류.
  대상키     TEXT NOT NULL,  -- 그 종류의 식별자 — 의안번호·회의id·의원코드·법령일련번호.
  실패종류   TEXT NOT NULL,  -- '없음' = 다시 물어도 같은 답(원천에 그 자료가 없다). 그 밖은 재시도 대상이라 다음 수집이 메울 수 있다.
  상세       TEXT,           -- NULL = 기록 없음.
  시도횟수   INTEGER NOT NULL DEFAULT 0,
  마지막시도 TEXT,           -- NULL = 기록 없음.
  PRIMARY KEY (코퍼스, 대상종류, 대상키)
);

CREATE TABLE 법률안대상법령 (
  -- **이 법률안이 고치려는 법.** 법률안 하나에 행이 여럿일 수도 하나도 없을 수도 있다. 빌드할 때 의안명·공포법률명을 법령명·법령약칭에 맞춰 계산한 표다.
  -- ⚠️ **법률안 ≠ 법.** 여기 이어진 것은 "통과되면 바뀔 법"이지 바뀐 결과가 아니다. 그 법의 지금 조문은 `법률안현행조문`, 그 법안이 어떻게 됐는지는 `의안.처리결과`·`공포일`.
  -- ⚠️ **미매칭(법령ID NULL) ≠ 그런 법이 없다.** 제정안(고칠 법이 애초에 없다)이 대부분이고, 통칭·구법명, 개정으로 법 이름 자체가 바뀌는 경우, "○○법 등 8개 법률" 같은 일괄개정도 여기 들어온다. 실측 6.9%(20,723건 중 1,440건).
  의안번호 TEXT NOT NULL REFERENCES 의안(의안번호) ON DELETE CASCADE,
  법령ID   TEXT,  -- NULL = 미매칭.
  법령명   TEXT,  -- 매칭된 법의 **현재** 이름 — 의안명 속 이름과 다를 수 있다(그 개정이 법 이름을 바꾸는 개정이면 갈린다). NULL = 미매칭.
  매칭근거 TEXT NOT NULL CHECK (매칭근거 IN ('공포법률명','의안명','미매칭')),  -- '공포법률명'이 가장 믿을 만하다(공포된 법안만 갖는 필드라 법제처 표기에 맞춰 온다). '의안명'은 이름 정규화로 맞춘 것이다.
  UNIQUE (의안번호, 법령ID)
);
CREATE INDEX idx_대상법령_법령 ON 법률안대상법령(법령ID);

CREATE VIEW 법률안현행조문 AS
  -- **이 법률안이 고치려는 법의 지금 조문** 하나가 한 행이다. 법안 원문이 아니라 현행이라, "무엇을 고치려는 법안인가"를 볼 때 조인을 매번 조립하지 않게 한다.
  -- ⚠️ `법령일련번호`(MST)를 노출한다 — 한 법령ID 에 현행 판본이 여럿일 수 있어(`법령` 참조), MST 를 안 보고 세면 같은 조가 판본 수만큼 곱해진다.
  -- ⚠️ 미매칭 법률안은 여기 없다. 법률안 전체를 보려면 `법률안대상법령` 을 LEFT JOIN 한다.
  SELECT t.의안번호, b.의안명, b.제안일, b.처리결과, b.소관위원회,
         t.법령ID, c.법령명, c.법령일련번호, c.시행대기,
         c.조문번호, c.조문가지번호, c.조문제목, c.전문
    FROM 법률안대상법령 t
    JOIN 의안 b ON b.의안번호 = t.의안번호
    JOIN 현행조문 c ON c.법령ID = t.법령ID;

CREATE VIEW 의원사건 AS
  -- **한 의원이 남긴 것**을 발의·표결·발언 한 축에 놓은 사건 하나가 한 행이다. 의원코드를 걸어 쓴다: SELECT * FROM 의원사건 WHERE 의원코드 = ? ORDER BY 일자 DESC
  -- ⚠️ 셋을 직접 조인하면 행이 곱해진다(발의 1건 × 표결 300건이 300행이 된다). 그래서 UNION 이다.
  -- ⚠️ 일자 NULL 이 섞인다 — 제안일·의결일이 원천에 없는 행이 있고(`의안`·`표결집계`), ORDER BY 일자 는 그것들을 맨 앞으로 보낸다.
  -- ⚠️ 발언은 의원코드를 못 푼 행이 있어 여기서 빠진다 — 그 목록은 수집실패의 대상종류='발언의원'.
  SELECT p.의원코드, '발의' AS 종류, b.제안일 AS 일자, b.의안번호 AS 키,
         p.역할 || ' · ' || b.의안명 AS 요약
    FROM 발의자 p JOIN 의안 b ON b.의안번호 = p.의안번호
  UNION ALL
  SELECT v.의원코드, '표결',
         (SELECT MIN(g.의결일) FROM 표결집계 g WHERE g.의안번호 = v.의안번호),
         v.의안번호, v.표결결과 || ' · ' || b.의안명
    FROM 표결 v JOIN 의안 b ON b.의안번호 = v.의안번호
  UNION ALL
  SELECT s.의원코드, '발언', m.회의일자,
         CAST(s.회의id AS TEXT) || '#' || s.순서,
         m.위원회명 || ' · ' || substr(s.내용, 1, 150)
    FROM 발언 s JOIN 회의 m ON m.회의id = s.회의id
   WHERE s.의원코드 IS NOT NULL;
"""


# ── 법률안 → 대상 법령 매칭 ─────────────────────────────────────────────────

# 끝에 붙는 의안 표지. 순서가 중요하다 — '법률안' 이 '일부개정법률안' 의 접미이므로 긴 것부터 본다.
_접미 = (
    ("일부개정법률안", ""),
    ("전부개정법률안", ""),
    ("폐지법률안", ""),
    ("법률안", "법률"),
    ("법안", "법"),
)
_대안표지 = re.compile(r"\(대안\)$")
_공백 = re.compile(r"\s+")


def 법령명추출(이름: str | None) -> str | None:
    """의안명·공포법률명에서 **법 이름의 정규형**을 뽑는다. 못 뽑으면 None.

    맞춰야 하는 표기 차이가 셋이다.
      - 끝의 `(대안)` 표지 — 위원장 대안은 이름 끝에 붙는다(실측 837건).
      - 공백 — 공포법률명은 공백을 다 뗀 채 온다('성폭력방지및피해자보호등에관한법률').
      - 가운뎃점 — `ㆍ`(U+318D)와 `·`(U+00B7)가 섞인다.

    ⚠️ **괄호를 통째로 지우지 마라.** `이스포츠(전자스포츠) 진흥에 관한 법률` 처럼 괄호가
       정식 이름의 일부인 법이 있다. 끝의 `(대안)` 만 다룬다.
    """
    if not 이름:
        return None
    s = _대안표지.sub("", 이름.strip()).strip()
    for 접미, 대체 in _접미:
        if s.endswith(접미):
            s = s[: len(s) - len(접미)] + 대체
            break
    return _공백.sub("", s.replace("ㆍ", "·")) or None


def 법령사전(법령conn: sqlite3.Connection) -> dict[str, tuple[str, str]]:
    """정규형 이름 → (법령ID, 법령명). **법률만, 법령ID 로 접어서.**

    `법종구분='법률'` 로 좁히는 이유: 법률안이 대통령령·부령에 붙을 여지를 없앤다. 실측에서
    좁혀도 매칭 결과가 한 건도 안 바뀌면서 사전이 8,213 → 2,823 키로 준다.

    현행뿐 아니라 **시행예정도 넣는다.** 공포됐지만 아직 시행 전인 법을 고치는 법률안이 실재한다
    (실측 105건). "대상 법령"은 현행 여부와 무관하다.

    한 법령ID 의 여러 판본은 이름이 같으므로 접힌다. 이름이 갈리는 판본은 각각 키를 갖는데,
    그 경우 뒤에 온 쪽이 이긴다 — 현행을 나중에 넣어 현행 이름이 남게 한다.
    """
    사전: dict[str, tuple[str, str]] = {}
    질의 = ("SELECT 법령ID, 법령명, 법령약칭 FROM 법령 WHERE 법종구분='법률'"
            " ORDER BY CASE 현행연혁코드 WHEN '현행' THEN 1 ELSE 0 END")
    for 법령ID, 법령명, 약칭 in 법령conn.execute(질의):
        for 표기 in (약칭, 법령명):
            if 키 := 법령명추출(표기):
                사전[키] = (법령ID, 법령명)
    return 사전


def 매칭(의안명: str, 공포법률명: str | None,
        사전: dict[str, tuple[str, str]]) -> tuple[str | None, str | None, str]:
    """(법령ID, 법령명, 매칭근거). 못 맞추면 (None, None, '미매칭').

    **공포법률명을 먼저 본다.** 그쪽이 법제처 표기에 맞춰 온 이름이라 더 믿을 만하다 — 공포법률명이
    한자(`行政訴訟法`)로 온 실측 사례가 있어 실패할 수 있고, 그때 의안명으로 떨어진다.
    """
    for 근거, 이름 in (("공포법률명", 공포법률명), ("의안명", 의안명)):
        if (키 := 법령명추출(이름)) and (맞음 := 사전.get(키)):
            return 맞음[0], 맞음[1], 근거
    return None, None, "미매칭"


# ── 락 ──────────────────────────────────────────────────────────────────────

class 락못잡음(RuntimeError):
    """원천 락을 잡지 못했다 — 수집기가 돌고 있다."""


def _락획득(락경로들: list[Path], 타임아웃: float):
    """원천 락 파일들에 `flock` 을 **고정 순서로 실제 획득**한다. 컨텍스트 매니저.

    ⚠️ **락 파일의 존재 여부로 판단하면 안 된다.** 수집기는 파일을 지우지 않고 닫으므로,
       존재를 보고 물러나면 첫 수집 이후 영영 못 돈다. 실제로 `flock` 을 건다.
    ⚠️ **순서를 고정한다.** 국회 → 법령. 두 프로세스가 반대 순서로 잡으면 데드락이다.
    """
    import contextlib

    @contextlib.contextmanager
    def _열기():
        열린것 = []
        마감 = time.monotonic() + 타임아웃
        try:
            for p in 락경로들:
                p.parent.mkdir(parents=True, exist_ok=True)
                f = p.open("a+")
                열린것.append(f)
                while True:
                    try:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= 마감:
                            raise 락못잡음(f"{p} 를 {타임아웃:.0f}초 안에 잡지 못했다 — 수집기가 돌고 있다")
                        time.sleep(0.5)
            yield
        finally:
            for f in reversed(열린것):
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                finally:
                    f.close()

    return _열기()


# ── 원천 열기 ───────────────────────────────────────────────────────────────

def 읽기URI(경로: Path) -> str:
    """읽기 전용 URI. 사이드카가 없을 때만 `immutable=1` 을 얹는다.

    ⚠️ **`mode=ro` 는 -wal 없는 WAL DB 를 못 연다(errno 14).** 수집기가 정상 종료하면 -wal 이
       지워지므로 라이브 원천이 정확히 그 상태다. `immutable=1` 이 그 자리를 대신한다.
    ⚠️ **거꾸로, `immutable=1` 을 -wal 이 있는 DB 에 걸면 마지막 체크포인트 이후의 적재가 안
       보인다** — 에러 없이 낡은 답이 나온다. 그래서 둘 중 하나를 사이드카로 고른다.

    이 판정은 락을 쥔 뒤에 해야 뜻이 있다. 안 쥐고 하면 판정과 사용 사이에 수집기가 -wal 을
    만들 수 있다.
    """
    사이드카 = any((경로.with_name(경로.name + s)).exists()
                 for s in ("-wal", "-journal", "-shm"))
    return f"file:{경로}?mode=ro" + ("" if 사이드카 else "&immutable=1")


def 원천열기(경로: Path) -> sqlite3.Connection:
    if not 경로.is_file():
        raise FileNotFoundError(f"원천이 없다: {경로}")
    return sqlite3.connect(읽기URI(경로), uri=True)


# ── DDL 수집과 주석 패치 ────────────────────────────────────────────────────

def _DDL(conn: sqlite3.Connection, 표들: tuple[str, ...]) -> tuple[list[str], list[str], list[str]]:
    """(표 DDL, 인덱스 DDL, 뷰 DDL). 자동 인덱스(`sqlite_autoindex_*`)는 sql 이 NULL 이라 빠진다."""
    자리 = ",".join("?" * len(표들))
    표 = [r[0] for r in conn.execute(
        f"SELECT sql FROM sqlite_master WHERE type='table' AND name IN ({자리})"
        " AND sql IS NOT NULL ORDER BY rowid", 표들)]
    인덱스 = [r[0] for r in conn.execute(
        f"SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name IN ({자리})"
        " AND sql IS NOT NULL ORDER BY rowid", 표들)]
    # 뷰는 이름으로 거르지 않고 다 가져온다 — 뷰가 참조하는 표가 `표들` 밖이면 어차피 만들 때
    # 터지므로, 거르는 대신 터지게 두는 편이 "조용히 빠진 뷰"보다 낫다.
    뷰 = [r[0] for r in conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='view' AND sql IS NOT NULL ORDER BY rowid")]
    return 표, 인덱스, 뷰


def _쪼갬(ddl: list[str], n: int) -> tuple[list[str], list[str]]:
    return ddl[:n], ddl[n:]


def 주석적용(코퍼스: str, ddl: list[str]) -> list[str]:
    """이 코퍼스의 주석 패치를 건다. 대상이 없거나 여럿이면 `ValueError`.

    빌드를 세우는 것이 요점이다 — 패치가 조용히 넘어가면 우리는 이미 없는 주석을 고치고 있다고
    믿게 되고, 그 사이 원천의 새 주석은 손 안 댄 채 스냅샷에 실린다.
    """
    합본 = "\n@@\n".join(ddl)
    for c, 찾을것, 바꿀것 in 주석패치:
        if c != 코퍼스:
            continue
        n = 합본.count(찾을것)
        if n != 1:
            raise ValueError(
                f"[{코퍼스}] 주석 패치의 대상이 {n}번 나온다(1번이어야 한다). 원천 주석이 바뀌었다면"
                f" 이 패치를 새 문장에 맞춰 고쳐라:\n  {찾을것[:120]}")
        합본 = 합본.replace(찾을것, 바꿀것)
    return 합본.split("\n@@\n")


# ── 빌드 ────────────────────────────────────────────────────────────────────

def _표복사(대상: sqlite3.Connection, 별칭: str, 표들: tuple[str, ...]) -> dict[str, int]:
    """ATTACH 된 원천에서 표를 통째로 옮긴다. 표별 행 수를 돌려준다."""
    수: dict[str, int] = {}
    for t in 표들:
        대상.execute(f'INSERT INTO "{t}" SELECT * FROM {별칭}."{t}"')
        수[t] = 대상.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    return 수


def _신선도(대상: sqlite3.Connection, 빌드시각: str) -> None:
    """두 원장(`수집상태`·`메타`)과 빌드 시각을 한 표로 접는다."""
    # 국회 — 적재 기준은 의안의 마지막 수집 시각이다(수집기 자신이 `적재헤더` 에서 그렇게 센다).
    시각 = 대상.execute("SELECT MAX(수집시각) FROM 의안").fetchone()[0]
    감사 = 대상.execute(
        "SELECT 상태, 건수, 상세 FROM 국회.수집상태 WHERE 대상='감사'"
        " ORDER BY 갱신일시 DESC LIMIT 1").fetchone()
    통과 = 상세 = None
    if 감사:
        상태, 건수, 감사상세 = 감사
        통과 = 1 if (상태 == "완료" and not 건수) else 0
        상세 = 감사상세 or (f"게이트 위반 {건수}건" if 건수 else None)
    건너뜀 = [r[0] for r in 대상.execute(
        "SELECT 대상 FROM 국회.수집상태 WHERE 상태='건너뜀'")]
    경고 = None
    if 건너뜀:
        경고 = ("마지막 수집이 " + "·".join(건너뜀) + " 를 건너뛰었다 — 정당·현직여부·위원회 배정이"
                " 옛 값일 수 있다. 발의·표결·발언 이력은 영향받지 않는다.")
    대상.execute("INSERT INTO 신선도 VALUES ('국회',?,?,?,?,?)", (시각, 통과, 상세, 경고, 빌드시각))

    # 법령 — `메타` 가 키·값이다.
    메타 = dict(대상.execute("SELECT 키, 값 FROM 법령.메타"))
    상태 = 메타.get("마지막감사")
    대상.execute("INSERT INTO 신선도 VALUES ('법령',?,?,?,?,?)", (
        메타.get("마지막수집일시"),
        None if 상태 is None else int(상태 == "통과"),
        None if 상태 in (None, "통과") else 상태,
        None,
        빌드시각,
    ))


def _수집실패(대상: sqlite3.Connection) -> None:
    """두 원장을 컬럼만 맞춰 합친다. 같은 뜻(커버리지의 구멍)이고 컬럼 이름만 달랐다."""
    대상.execute(
        "INSERT INTO 수집실패 SELECT '국회', 대상종류, 대상키, 실패종류, 상세, 시도횟수, 마지막시도"
        " FROM 국회.수집실패")
    대상.execute(
        "INSERT INTO 수집실패 SELECT '법령', 자료종류, 자료ID, 실패종류, 메시지, 시도횟수, 최종시도일시"
        " FROM 법령.수집실패")


def _법률안대상법령(대상: sqlite3.Connection) -> dict[str, int]:
    사전 = 법령사전(대상)
    행 = 대상.execute(
        "SELECT 의안번호, 의안명, 공포법률명 FROM 의안 WHERE 의안종류='법률안'").fetchall()
    묶음, 셈 = [], {"공포법률명": 0, "의안명": 0, "미매칭": 0}
    for 번호, 의안명, 공포 in 행:
        법령ID, 법령명, 근거 = 매칭(의안명, 공포, 사전)
        셈[근거] += 1
        묶음.append((번호, 법령ID, 법령명, 근거))
    대상.executemany("INSERT OR IGNORE INTO 법률안대상법령 VALUES (?,?,?,?)", 묶음)
    셈["법률안"] = len(행)
    return 셈


def build(원천국회: Path, 원천법령: Path, 소관기관: Path, 출력: Path,
          *, 락타임아웃: float = 600.0) -> dict:
    """스냅샷을 만들어 `출력` 자리에 원자적으로 놓는다. 결과 요약 dict 를 돌려준다.

    검증을 통과하지 못하면 **기존 출력 파일은 그대로 둔다.** 어제 것이 남는 편이 오늘 것이
    반쯤 만들어져 있는 것보다 낫다 — 반쯤 만들어진 파일은 조회하는 쪽에서 정상으로 보인다.
    """
    시작 = time.monotonic()
    빌드시각 = now_kst()
    for p in (원천국회, 원천법령, 소관기관):
        if not p.is_file():
            raise FileNotFoundError(f"원천이 없다: {p}")

    임시 = 출력.with_name(출력.name + f".빌드중-{os.getpid()}")
    임시.unlink(missing_ok=True)
    출력.parent.mkdir(parents=True, exist_ok=True)

    락들 = [Path(str(원천국회) + ".lock"), Path(str(원천법령) + ".lock")]
    with _락획득(락들, 락타임아웃):
        국회 = 원천열기(원천국회)
        법령 = 원천열기(원천법령)
        기관 = 원천열기(소관기관)
        try:
            국회DDL, 국회인덱스, 국회뷰 = _DDL(국회, 국회표)
            법령DDL, 법령인덱스, 법령뷰 = _DDL(법령, 법령표)
            # `소관기관` 은 자기 DB 가 진실 원천이라 DDL 째 온다 — 주석도 저쪽에서 관리한다.
            기관DDL, _, _ = _DDL(기관, ("소관기관", ))
        finally:
            국회.close()
            법령.close()
            기관.close()

        # 패치는 표와 뷰를 함께 본다 — 관리자 좌표는 뷰 주석에도 있다(`의안회의`).
        n = len(국회DDL)
        국회DDL, 국회뷰 = _쪼갬(주석적용("국회", 국회DDL + 국회뷰), n)
        n = len(법령DDL)
        법령DDL, 법령뷰 = _쪼갬(주석적용("법령", 법령DDL + 법령뷰), n)

        # ⚠️ **`uri=True` 로 연다.** `ATTACH DATABASE` 는 이 연결이 URI 모드로 열렸을 때만
        #    `file:…?mode=ro` 를 URI 로 읽는다. 아니면 그 문자열을 **파일 이름 그대로** 찾다가
        #    "unable to open database" 로 죽는다.
        c = sqlite3.connect(f"file:{임시}", uri=True)
        try:
            # ⚠️ FK 를 끈다. 원천 `sqlite_master` 순서는 `조문요소` 가 `조문` 보다 앞이라
            #    켠 채 순서대로 넣으면 실패한다. 다 넣은 뒤 `foreign_key_check` 로 한 번에 본다.
            c.execute("PRAGMA foreign_keys=OFF")
            c.execute("PRAGMA journal_mode=OFF")
            c.execute("PRAGMA synchronous=OFF")
            for sql in 국회DDL + 법령DDL + 기관DDL:
                c.execute(sql)
            c.executescript(SCHEMA)

            c.execute("ATTACH DATABASE ? AS 국회", (읽기URI(원천국회), ))
            c.execute("ATTACH DATABASE ? AS 법령", (읽기URI(원천법령), ))
            c.execute("ATTACH DATABASE ? AS 기관", (읽기URI(소관기관), ))
            행수 = _표복사(c, "국회", 국회표)
            행수 |= _표복사(c, "법령", 법령표)
            c.execute("INSERT INTO 소관기관 SELECT * FROM 기관.소관기관")
            행수["소관기관"] = c.execute("SELECT COUNT(*) FROM 소관기관").fetchone()[0]
            _수집실패(c)
            행수["수집실패"] = c.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0]
            _신선도(c, 빌드시각)
            c.execute(
                "INSERT INTO 신선도 VALUES ('소관기관',NULL,NULL,NULL,"
                "'한 시점의 스냅샷이고 이력이 없다. 정부조직 개편으로 낡는다.',?)", (빌드시각, ))
            매칭셈 = _법률안대상법령(c)
            행수["법률안대상법령"] = c.execute(
                "SELECT COUNT(*) FROM 법률안대상법령").fetchone()[0]

            # 인덱스·뷰는 데이터가 다 들어간 뒤에 만든다 — 적재 중 인덱스 유지가 훨씬 느리다.
            for sql in 국회인덱스 + 법령인덱스:
                c.execute(sql)
            for sql in 국회뷰 + 법령뷰:
                c.execute(sql)

            원천행수 = {}
            for 별칭, 표들 in (("국회", 국회표), ("법령", 법령표)):
                for t in 표들:
                    원천행수[t] = c.execute(
                        f'SELECT COUNT(*) FROM {별칭}."{t}"').fetchone()[0]
            c.commit()
            for 별칭 in ("국회", "법령", "기관"):
                c.execute(f"DETACH DATABASE {별칭}")

            어긋남 = {t: (원천행수[t], 행수[t]) for t in 원천행수 if 원천행수[t] != 행수[t]}
            if 어긋남:
                raise RuntimeError(f"표별 행 수가 원천과 다르다: {어긋남}")
            if (r := c.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
                raise RuntimeError(f"integrity_check 실패: {r}")
            if 위반 := c.execute("PRAGMA foreign_key_check").fetchall():
                raise RuntimeError(f"foreign_key_check {len(위반)}건 위반: {위반[:5]}")
        except BaseException:
            c.close()
            임시.unlink(missing_ok=True)
            raise
        c.close()

    # 여기까지 왔으면 검증을 다 통과했다. `os.replace` 는 같은 파일시스템에서 원자적이라,
    # 조회하는 쪽은 어제 파일이나 오늘 파일을 보지 그 사이를 보지 않는다.
    os.replace(임시, 출력)
    return {
        "출력": str(출력),
        "크기": 출력.stat().st_size,
        "빌드시각": 빌드시각,
        "소요초": round(time.monotonic() - 시작, 1),
        "행수": 행수,
        "매칭": 매칭셈,
    }


def _main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="수집기의 원천 DB 둘과 소관기관 DB 를 읽기 전용 통합 스냅샷으로 접는다.",
        epilog="검증(표별 행 수 · integrity_check · foreign_key_check)을 다 통과해야 출력을 교체한다."
               " 못 통과하면 기존 파일이 그대로 남고 rc 1 이다. 원천 락을 못 잡으면 rc 3.")
    ap.add_argument("--원천국회", required=True, type=Path)
    ap.add_argument("--원천법령", required=True, type=Path)
    ap.add_argument("--소관기관", required=True, type=Path)
    ap.add_argument("--출력", required=True, type=Path)
    ap.add_argument("--락타임아웃", type=float, default=600.0,
                    help="원천 락을 기다리는 초. 기본 600 — 수집 한 패스가 끝나기를 기다린다.")
    a = ap.parse_args(argv)
    try:
        결과 = build(a.원천국회, a.원천법령, a.소관기관, a.출력, 락타임아웃=a.락타임아웃)
    except 락못잡음 as e:
        print(json.dumps({"실패": str(e)}, ensure_ascii=False))
        return 3
    except Exception as e:
        print(json.dumps({"실패": f"{type(e).__name__}: {e}"}, ensure_ascii=False))
        return 1
    print(json.dumps(결과, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
