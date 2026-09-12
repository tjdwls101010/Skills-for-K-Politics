#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""22대 국회 DB 의 스키마와 쓰기 경로.

**이 파일의 `SCHEMA` 가 스키마의 원본이다.** 이 DB 는 주석이 곧 문서라(SQLite 가
`CREATE TABLE` 괄호 안의 `--` 주석을 `sqlite_master.sql` 에 텍스트로 보존한다) 여기 적은
주석이 그대로 조회자에게 간다 — **컬럼을 늘리면서 주석을 안 쓰면 그 컬럼은 설명이 없다.**
사본을 따로 두지 않는 이유도 그것이다: 두 곳이 갈리면 어느 쪽이 맞는지 아무도 모른다.

**그래서 SQL 문자열 안팎이 독자의 경계다** — `SCHEMA` 안의 `--` 주석은 **조회자**가 읽고,
파이썬 주석과 docstring 은 **이 파일을 고치는 사람**이 읽는다. 적재 방식·게이트 설계·
`ALTER TABLE` 의 함정처럼 조회 결과를 안 바꾸는 것은 바깥에 적는다. 안에 두면 조회자가
매 질의마다 자기와 무관한 것을 읽고, 정작 함정은 그 사이에 묻힌다. 반대로 **조회 결과의
해석을 바꾸는 운영 상태**(`수집실패` 의 '없음', `수집상태` 의 '건너뜀')는 안에 남는다.

적재 설계에서 옮긴 기록:
- 의안.대안의안번호는 대안이 원안보다 늦게 접수되는 전방 참조라 FK를 두지 않았다.
- 회의의안.의안번호는 회의록이 의안보다 먼저 들어오거나 22대 밖 의안을 가리킬 수 있어 FK를 두지 않았다(connect docstring 참조).
- 의안심사는 상태 컬럼 대신 사건 행으로 쌓는다 — 반복 회부와 소위의 실질 논의를 보존하기 위해서다.
- 발언과 안건은 안건을 올린 뒤에도 서로 다른 이야기가 오가는 것이 통상이라 연결 컬럼을 두지 않았다.
- 의안.수집시각은 의안·의안심사 트랜잭션 커밋 시각을 KST로 기록한다 — SQLite datetime('now')는 UTC다.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

KST = timezone(timedelta(hours=9))

SKILL_DIR = Path(__file__).resolve().parents[2]   # Scripts/국회 → Scripts → k-politics

# ⚠️ 해석 순서를 여기서 못박는다. 환경변수가 먼저다 — GitHub Actions 가 DB 를 체크아웃 **밖**으로
#    보내는 유일한 수단이 이것이고, `actions/checkout` 의 `git clean -ffdx` 는 gitignore 된
#    파일까지 지우므로 작업공간 안에 DB 를 두면 매 실행 첫 스텝이 수집물을 날린다. 에러는 안 난다.
DB_ENV = "CONGRESS_DB"


def db_path(explicit: str | os.PathLike[str] | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get(DB_ENV)
    return Path(env) if env else SKILL_DIR / "DBs" / "CONGRESS.db"


SCHEMA = r"""
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS 의안 (
    -- 22대에 접수된 모든 의안 — 법률안뿐 아니라 예산안·결의안·동의안·규칙안까지라, 법률안만 보려면 의안종류='법률안' 을 건다.
    의안번호            TEXT PRIMARY KEY,  -- '2220259'처럼 보도자료와 대조되는 키.
    의안ID              TEXT NOT NULL UNIQUE,  -- 표결·대안정보 요청용 핸들 — 의안의 동일성은 의안번호로 구분한다.
    의안종류            TEXT,  -- NULL = 아직 상세를 안 받은 비법률안(다음 수집의 재수집 대상).
    의안명              TEXT NOT NULL,  -- 위원장 대안은 끝에 '(대안)'이 붙는다.
    제안자구분          TEXT
                        CHECK (제안자구분 IN ('의원','위원장','정부','의장','기타')),  -- '의원'은 발의자, '위원장'은 소관위원회, '정부'·'의장'은 그 자체가 제안 주체 — 법률안제안주체.
    -- ⚠️ NULL = 원천이 CHECK 밖 값을 준 자리 — 수집실패의 '막힘'.
    -- ⚠️ '정부'는 부처를 특정하지 않는다 — 소관위원회는 대리물이다.
    제안일              TEXT,  -- NULL = 원천값 없음.
    소관위원회          TEXT REFERENCES 위원회(위원회명),  -- ⚠️ 현재 소관이며 회부 전이면 NULL — 원천이 한 벌만 주므로 위원회 이관 이력은 담지 않는다.
    제안이유및주요내용  TEXT,  -- 키워드 검색 대상인 평문 — 조문 원문은 담지 않는다.
    -- ⚠️ NULL = 미수집 또는 원천이 비어 있음.
    처리결과            TEXT,  -- 본회의 최초 심의결과(법률안·비법률안 모두) — NULL = 아직 본회의 심의 전 또는 원천 결측(표결집계에 행이 있는데 NULL 이면 결측).
    -- ⚠️ 재의를 반영하지 않아 원안가결 뒤 거부권으로 부결된 의안도 있다 — 공포 여부는 공포일, 거부권 기록은 의안심사의 '재의'.
    -- 열린 값 집합의 분포는 audit R1.
    대안의안번호        TEXT,  -- 이 의안을 흡수한 대안의 번호 — NULL = 관계 없음.
    정부이송일          TEXT,  -- NULL = 미발생 또는 원천값 없음.
    공포일              TEXT,  -- NULL = 미발생 또는 원천값 없음.
    공포법률명          TEXT,  -- ⚠️ 의안명과 다르며 법제처 등 바깥 자료와 대조할 이름이다.
    -- ⚠️ NULL = 공포 전 또는 원천값 없음 — 공포일이 있어도 비어 온 실측이 있다(일괄개정).
    공포번호            TEXT,  -- NULL = 미발생 또는 원천값 없음.
    수집시각            TEXT  -- ⚠️ 의안·의안심사 커밋 시각(KST), NULL = 기록 없음 — 별도 패스인 제안이유·발의자·표결·대안의 수집 완료는 뜻하지 않는다.
);
CREATE TABLE IF NOT EXISTS 의안심사 (
    -- 법률안의 심사 사건 — 같은 단계에 회부 행이 여럿일 수 있고 실질 논의는 소위에서 일어난다.
    -- ⚠️ 위원장 대안·정부제출은 회부 행이 없을 수 있다 — INNER JOIN 은 법이 될 가능성이 높은 법안을 뺀다.
    -- ⚠️ 비법률안은 원천 범위 밖이라 행이 없다 — 논의는 의안회의, 본회의 표결 결과는 표결집계.
    의안번호    TEXT NOT NULL REFERENCES 의안(의안번호) ON DELETE CASCADE,
    단계        TEXT NOT NULL CHECK (단계 IN ('소관위','소위','법사위','본회의','재의')),  -- 접수·정부이송·공포는 의안의 컬럼에 있다.
    -- ⚠️ '재의'가 거부권의 유일한 기록이며 의안.처리결과에는 반영되지 않는다.
    회차        INTEGER NOT NULL DEFAULT 1,  -- 원천 응답 순서이며 시간순이 아니다 — 시간순은 회부일·처리일로 구분한다.
    위원회명    TEXT REFERENCES 위원회(위원회명),  -- ⚠️ NULL = 원천에 위원회 칸이 없거나(본회의·재의) 위원회명이 결측인 자리(소위 절반가량, 드물게 소관위) — 상위 위원회명으로 대체된 값이 아니다.
    회부일      TEXT,  -- 계류 법안의 시간 표지 — NULL = 본회의처럼 원천에 해당 사실이 없거나 미발생·결측.
    상정일      TEXT,  -- NULL = 미발생 또는 결측.
    처리일      TEXT,  -- ⚠️ NULL = 미발생 또는 결측 — 의안에 처리결과가 있어도 소관위 처리일은 비어 있을 수 있다.
    처리결과    TEXT,  -- 단계별 결과이며 의안.처리결과와 어휘도 다르다 — NULL = 미발생 또는 결측.
    PRIMARY KEY (의안번호, 단계, 회차)
);
CREATE TABLE IF NOT EXISTS 발의자 (
    -- 의원 발의자 — 의안 하나에 대표·공동발의 행이 여럿이다.
    -- ⚠️ 위원장·정부·의장 발의에는 개별 의원 발의자가 없어 이 표와의 조인에서 빠진다 — 법률안제안주체.
    의안번호    TEXT NOT NULL REFERENCES 의안(의안번호) ON DELETE CASCADE,
    의원코드    TEXT NOT NULL REFERENCES 의원(의원코드),
    역할        TEXT NOT NULL CHECK (역할 IN ('대표발의','공동발의')),  -- ⚠️ 공동대표발의가 있어 의안 하나에 역할='대표발의'가 둘 이상일 수 있다.
    PRIMARY KEY (의안번호, 의원코드)
);
CREATE TABLE IF NOT EXISTS 표결집계 (
    -- 본회의 표결의 공식 집계 — 행이 있는 의안은 본회의 표결을 거쳤다.
    -- ⚠️ 표결 명단이 공식 집계보다 적은 의안이 있다 — 몇 대 몇인지는 이 표, 누가 어떻게 던졌는지는 표결.
    의안번호    TEXT NOT NULL REFERENCES 의안(의안번호) ON DELETE CASCADE,
    의결일      TEXT NOT NULL,
    재적수      INTEGER,  -- NULL = 원천값 없음.
    투표수      INTEGER,  -- NULL = 원천값 없음.
    찬성수      INTEGER,  -- NULL = 원천값 없음.
    반대수      INTEGER,  -- NULL = 원천값 없음.
    기권수      INTEGER,  -- NULL = 원천값 없음.
    PRIMARY KEY (의안번호, 의결일)
);
CREATE TABLE IF NOT EXISTS 표결 (
    -- 본회의 표결의 의원별 명단.
    -- ⚠️ 재의 표결 명단은 원천에 없다 — 재의가 있었다는 사실은 의안심사.
    의안번호    TEXT NOT NULL REFERENCES 의안(의안번호) ON DELETE CASCADE,
    의원코드    TEXT NOT NULL REFERENCES 의원(의원코드),
    표결결과    TEXT NOT NULL CHECK (표결결과 IN ('찬성','반대','기권','불참')),  -- '불참'도 명단에 포함되므로 명단 행 수는 투표수가 아니다.
    PRIMARY KEY (의안번호, 의원코드)
);
CREATE TABLE IF NOT EXISTS 회의 (
    -- 본문을 받아 파싱에 성공한 회의 — 본회의는 담지 않으며 열거됐으나 본문을 못 받은 회의는 수집실패에 있다.
    -- ⚠️ 출석자 명부는 없다 — 발언에 있는 것은 말을 한 사람뿐이다.
    회의id      INTEGER PRIMARY KEY,  -- 회의록 뷰어 id — https://record.assembly.go.kr/assembly/viewer/minutes/xml.do?id={이값}&type=view
    회의코드    TEXT,  -- OpenAPI 식별자로 회의id와 다른 체계 — NULL = 원천값 없음.
    회의종류    TEXT NOT NULL
                CHECK (회의종류 IN ('상임위원회','특별위원회','예산결산특별위원회','국정감사')),  -- 위원회의 종류가 아니라 회의의 종류 — 같은 위원회가 상임위원회 회의와 국정감사를 모두 연다.
    위원회명    TEXT NOT NULL REFERENCES 위원회(위원회명),  -- 소위 회의는 소위원회 이름 — 상위는 위원회.상위위원회.
    회기        INTEGER,  -- NULL = 원천값 없음.
    차수        INTEGER,  -- ⚠️ NULL = 국정감사 또는 원천값 없음.
    회의일자    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 발언 (
    -- 회의 안에서 순서를 보존한 발언과 화자 정보.
    -- ⚠️ 발언과 안건의 연결은 없다 — 의안회의로 찾은 것은 그 법안이 안건인 회의의 발언이지 그 법안에 관한 발언이 아니다.
    회의id      INTEGER NOT NULL REFERENCES 회의(회의id) ON DELETE CASCADE,
    순서        INTEGER NOT NULL,  -- 회의 안 발언 순서(1부터) — 질의·답변 구분 컬럼은 없으며 주변 발언은 순서 BETWEEN ?-3 AND ?+3으로 잇는다.
    발언자명    TEXT NOT NULL,
    직위        TEXT,  -- 사람이 아니라 회의×사람의 속성 — 부처 답변의 진입점이며 NULL = 직위 정보 없음.
    -- ⚠️ 증인·참고인은 기관명이 없는 '증인'·'참고인'·'증인(이름)변호인' 류라 부처명 검색에 잡히지 않는다.
    의원코드    TEXT REFERENCES 의원(의원코드),  -- NULL = 이 발언자를 의원으로 풀지 못함 — 대개 장관·차관·청장·전문위원·증인·참고인 등 비의원이다.
    -- ⚠️ 의원 이름과 같아도 코드가 NULL인 행이 있어 NULL을 모두 비의원으로 세면 의원 발언이 빠진다 — 수집실패의 '발언의원'.
    내용        TEXT NOT NULL,
    PRIMARY KEY (회의id, 순서)
);
CREATE TABLE IF NOT EXISTS 회의의안 (
    -- 의안과 회의의 연결 — 같은 의안이 여러 안건으로 올라도 한 행이며 안건 순서는 담지 않는다.
    -- ⚠️ 국정감사는 행이 없어도 정상이며, 간사 선임·업무보고·현안질의·인사청문회 등 의안 아닌 안건은 담지 않는다.
    -- ⚠️ 위원장 대안은 심사 당일 새 번호를 받아 자기 번호의 연결이 없는 것으로 관측됐다 — 원안 경유는 의안회의, 의안에 없는 번호는 audit R7.
    회의id      INTEGER NOT NULL REFERENCES 회의(회의id) ON DELETE CASCADE,
    의안번호    TEXT NOT NULL,  -- 의안에 없는 번호가 포함될 수 있다 — audit R7.
    PRIMARY KEY (회의id, 의안번호)
);
CREATE TABLE IF NOT EXISTS 위원회 (
    -- 의안·회의·의원이 참조하는 위원회 이름 차원.
    위원회명    TEXT PRIMARY KEY,  -- ⚠️ 소위원회는 '상위 위원회 + 공백 + 소위명' 형태다(보건복지위원회 법안심사제1소위원회) — 이름의 실제 표기는 위원회명 DISTINCT.
    -- ⚠️ 개편 전후 이름이 함께 남아 새 이름만 걸면 옛 이름으로 회부된 의안이 빠진다.
    상위위원회  TEXT REFERENCES 위원회(위원회명)  -- ⚠️ NULL = 소위 아님 또는 상위 미상 — 상위 없이 등록된 '…소위' 별칭도 있다(audit R18).
);
CREATE TABLE IF NOT EXISTS 의원 (
    -- 22대를 거쳐 간 전원 — 사퇴·의원직 상실로 떠난 사람의 발의·표결·발언도 참조한다.
    의원코드    TEXT PRIMARY KEY,  -- OpenAPI 전역의 코드이며 회의록 쪽은 이 코드를 모른다.
    이름        TEXT NOT NULL,  -- ⚠️ 동명이인이 실재해 조인 키가 아니다 — 이름으로 집계하면 서로 다른 사람이 접힌다.
    정당        TEXT,  -- 현직 API에서 마지막으로 확인한 정당 — 확인한 적 없는 전직은 원천이 준 대수별 값, NULL = 원천값 없음.
    -- 표결 당시 당적은 담지 않는다.
    정당확인일  TEXT,  -- 현직 API에서 정당을 마지막으로 확인한 날짜 — NULL = 확인한 적 없음(대개 전직, 정당은 원천이 준 대수별 값), 현직의 확인 지연은 audit R17.
    선거구      TEXT,  -- 비례대표는 '비례대표'(NULL 아님) — NULL = 원천값 없음.
    현직여부    INTEGER NOT NULL DEFAULT 1  -- 0인 사람도 발의·표결·발언을 가지며 조회에서 빼면 그 이력이 사라진다.
);
CREATE TABLE IF NOT EXISTS 의원위원회 (
    -- 마지막 수집 시점의 위원회 명부 — 위원회를 옮긴 이력은 담지 않는다.
    의원코드    TEXT NOT NULL REFERENCES 의원(의원코드) ON DELETE CASCADE,
    위원회명    TEXT NOT NULL REFERENCES 위원회(위원회명),
    PRIMARY KEY (의원코드, 위원회명)
);
CREATE TABLE IF NOT EXISTS 수집상태 (
    -- 수집 패스의 결과와 현재 상태의 시작 시점.
    대상        TEXT NOT NULL,  -- '의원위원회' | '의원현직' | '감사'
    키          TEXT NOT NULL,  -- 현재 '전체'.
    상태        TEXT NOT NULL CHECK (상태 IN ('완료','건너뜀')),  -- ⚠️ '건너뜀'이면 그 대상(현직여부·정당·위원회 배정)은 옛 값을 그대로 들고 있다 — 상태 지속 기간은 audit R14.
    건수        INTEGER,  -- 명부 대상은 원천 행수, '감사'는 위반한 게이트 수 — NULL = 기록 없음.
    상세        TEXT,  -- NULL = 상세 기록 없음.
    갱신일시    TEXT NOT NULL,
    상태시작일시 TEXT,  -- 현재 상태가 시작된 시각이며 갱신일시와 다르다 — NULL = 기록 없음.
    PRIMARY KEY (대상, 키)
);
CREATE TABLE IF NOT EXISTS 수집실패 (
    -- 수집하지 못한 자료 — 남은 행은 해당 대상종류 자료의 불완전한 범위다.
    대상종류    TEXT NOT NULL
                CHECK (대상종류 IN ('의안상세','의안요약','의안대안','의안표결','발의자',
                                    '회의본문','회의의안','발언의원','의원')),
    대상키      TEXT NOT NULL,  -- 의안번호·회의id·의원코드.
    실패종류    TEXT NOT NULL CHECK (실패종류 IN ('재시도','없음','막힘','보류')),  -- '없음' = 다시 물어도 같은 답 — 원천에 없거나(404·INFO-200) 풀 정보가 없다(발언의원).
    -- '막힘' = 원천은 주지만 저장할 수 없음.
    -- '보류' = 사람이 확인하고 더는 묻지 않기로 한 자료 — audit A11에서는 빠지고 R6에 남으며 완결성의 한계다.
    상세        TEXT,  -- NULL = 상세 기록 없음.
    시도횟수    INTEGER NOT NULL DEFAULT 0,
    마지막시도  TEXT,  -- NULL = 시도 시각 기록 없음.
    PRIMARY KEY (대상종류, 대상키)
);

CREATE INDEX IF NOT EXISTS idx_의원_이름  ON 의원(이름);
CREATE INDEX IF NOT EXISTS idx_의원_정당  ON 의원(정당);
CREATE INDEX IF NOT EXISTS idx_의안_제안일   ON 의안(제안일);
CREATE INDEX IF NOT EXISTS idx_의안_종류     ON 의안(의안종류);
CREATE INDEX IF NOT EXISTS idx_의안_처리결과 ON 의안(처리결과);
CREATE INDEX IF NOT EXISTS idx_의안_소관위   ON 의안(소관위원회);
CREATE INDEX IF NOT EXISTS idx_의안_대안     ON 의안(대안의안번호);
CREATE INDEX IF NOT EXISTS idx_심사_위원회 ON 의안심사(위원회명, 회부일);
CREATE INDEX IF NOT EXISTS idx_심사_단계   ON 의안심사(단계, 처리일);
CREATE INDEX IF NOT EXISTS idx_발의자_의원 ON 발의자(의원코드, 역할);
CREATE INDEX IF NOT EXISTS idx_표결_의원 ON 표결(의원코드, 표결결과);
CREATE INDEX IF NOT EXISTS idx_회의_일자   ON 회의(회의일자);
CREATE INDEX IF NOT EXISTS idx_회의_위원회 ON 회의(위원회명, 회의일자);
CREATE INDEX IF NOT EXISTS idx_발언_의원 ON 발언(의원코드);
CREATE INDEX IF NOT EXISTS idx_발언_직위 ON 발언(직위);
CREATE INDEX IF NOT EXISTS idx_회의의안_의안 ON 회의의안(의안번호);

DROP VIEW IF EXISTS 법률안제안주체;
CREATE VIEW 법률안제안주체 AS
  -- 법률안의 제안 주체를 의원·위원장·정부·의장에 걸쳐 한 축으로 준다.
  -- ⚠️ 의안 하나에 행이 여럿일 수 있다(공동발의).
  SELECT b.의안번호, b.제안자구분, m.의원코드, m.이름 AS 주체, m.정당, p.역할
    FROM 의안 b
    JOIN 발의자 p ON p.의안번호 = b.의안번호
    JOIN 의원 m ON m.의원코드 = p.의원코드
   WHERE b.의안종류 = '법률안'
  UNION ALL
  SELECT b.의안번호, b.제안자구분, NULL, CASE b.제안자구분
           WHEN '위원장' THEN b.소관위원회 ELSE b.제안자구분 END, NULL, NULL
    FROM 의안 b
   WHERE b.의안종류 = '법률안' AND b.제안자구분 <> '의원';

DROP VIEW IF EXISTS 의안회의;
CREATE VIEW 의안회의 AS
  -- 의안이 안건으로 오른 회의 — 의안 아닌 안건의 범위는 회의의안과 같다.
  -- ⚠️ 자기 번호의 회의 연결이 없는 것으로 관측된 위원장 대안은 원안경유로 연결한다 — 회의의안의 미해소 번호는 audit R7.
  -- ⚠️ 원안 여럿이 같은 회의를 거치면 행이 여럿이다.
  SELECT h.의안번호, h.의안번호 AS 경유의안번호, h.회의id, m.회의종류, m.위원회명,
         m.회의일자, '직접' AS 경로
    FROM 회의의안 h JOIN 회의 m USING (회의id)
  UNION ALL
  SELECT d.대안의안번호, d.의안번호, h.회의id, m.회의종류, m.위원회명, m.회의일자, '원안경유'
    FROM 의안 d JOIN 회의의안 h ON h.의안번호 = d.의안번호
                JOIN 회의 m USING (회의id)
   WHERE d.대안의안번호 IS NOT NULL;

DROP VIEW IF EXISTS 신선도;
CREATE VIEW 신선도 AS
  -- **이 답은 언제 기준인가.** 항상 한 행이다 — 빈 DB 에서도 값이 NULL 인 한 행이 온다.
  -- 사용자가 "지금 어떻게 되어 있나"를 물을 때 그 '지금'이 며칠 전인지 모르면 낡은 시점의 답을 오늘 것인 양 내놓게 된다. 그래서 시점을 묻는 질의는 여기서 끝난다.
  SELECT (SELECT MAX(수집시각) FROM 의안) AS 적재기준시각,  -- 마지막으로 자료를 받은 시각(KST). NULL = 받은 기록이 없다.
         CASE WHEN g.상태 IS NULL THEN NULL WHEN g.상태 = '완료' AND COALESCE(g.건수, 0) = 0 THEN 1 ELSE 0 END AS 감사통과,  -- 1 = 게이트를 다 통과. NULL = 감사 기록 없음. **0 = 위반이 남아 있었다** — 이 DB 를 근거로 답할 때는 감사상세의 게이트가 무엇을 재는지 확인하거나, 못 하면 그 한계를 답에 밝힌다.
         g.갱신일시 AS 감사시각,  -- ⚠️ **그 감사가 언제 것인가.** 감사는 수집이 돌 때만 기록되므로 수집이 며칠 멈췄으면 이 시각도 그만큼 옛것이고, 적재기준시각과 벌어져 있으면 그 사이의 적재는 감사를 안 거쳤다.
         COALESCE(g.상세, CASE WHEN g.건수 > 0 THEN '게이트 위반 ' || g.건수 || '건' END) AS 감사상세,  -- 위반한 게이트와 건수. NULL = 위반 없음 또는 기록 없음.
         (SELECT CASE WHEN COUNT(*) > 0 THEN '마지막 수집이 ' || group_concat(대상, '·') || ' 를 건너뛰었다 — 정당·현직여부·위원회 배정이 옛 값일 수 있다. 발의·표결·발언 이력은 영향받지 않는다.' END
            FROM 수집상태 WHERE 상태 = '건너뜀') AS 경고  -- ⚠️ **어느 부분이 옛 값인가**를 문장으로. NULL = 그런 부분 없음.
    FROM (SELECT 1) LEFT JOIN 수집상태 g ON g.대상 = '감사' AND g.키 = '전체';
"""


def connect(path: str | os.PathLike[str] | None = None) -> sqlite3.Connection:
    """연결을 열고 **매번** PRAGMA 를 다시 건다.

    ⚠️ `journal_mode` 는 파일에 저장되지만 **`foreign_keys` 는 연결 속성이고 기본값이 OFF 다.**
    `SCHEMA` 안에 그 줄이 있어도 다음에 새로 연 연결에서는 꺼져 있다. 수집 스크립트가 각자
    별도 프로세스라 실행마다 새 연결이므로, 이 함수를 거치지 않는 경로가 하나라도 있으면
    **스키마의 FK 전부가 장식이 된다 — 아무 증상도 없이.**

    ⚠️ **그리고 `회의의안.의안번호` 에는 애초에 FK 가 없다** — 회의록이 의안보다 먼저
    들어올 수 있고 22대 밖 의안을 가리키기도 해서, FK 를 걸면 정상 수집이 터진다.
    미해소 건수는 게이트가 아니라 `audit.py` R7 보고값이다.
    """
    p = db_path(path)
    _유령방지(p, path)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _유령방지(p: Path, 명시) -> None:
    """**환경변수로 겨눈 DB 는 이미 있어야 한다.** 없으면 만들지 말고 즉시 실패한다.

    ⚠️ 위 `mkdir(parents=True)` 와 평범한 `connect()` 가 합쳐지면, 폴더를 옮기거나 경로를
       오타 낸 날 자동 수집이 **새 빈 DB 를 만들고 계속 초록으로 끝난다** — 감사는 전부
       현재 DB 안에서만 등식을 보므로 빈 DB 에서 다 통과하고, 아무 에러도 나지 않는다.

    `--db` 로 **환경변수와 다른** 경로를 손으로 댄 것은 막지 않는다.
    **처음 만들 때는 경로를 명시하라**가 탈출구다.

    ⚠️ **판정을 '인자가 None 인가'로 하면 안 된다.** `collect.py` 는 `db_path()` 로 환경변수를
       먼저 경로로 바꾼 뒤 그걸 인자로 넘긴다 — 그 판정이면 **주 수집 경로에만 구멍이 난다.**
       그래서 "어떻게 왔나"가 아니라 **"환경변수가 겨눈 바로 그 파일인가"**를 묻는다.
    """
    env = os.environ.get(DB_ENV)
    겨눈것 = 명시 is None or (env and Path(env).expanduser() == Path(명시).expanduser())
    if 겨눈것 and env and not p.exists():
        raise SystemExit(
            f"🔴 ${DB_ENV} 가 가리키는 DB 가 없다: {p}\n"
            f"   새 빈 DB 를 만들지 않는다 — 유령 DB 가 생기면 수집은 매일 초록인데\n"
            f"   실제 DB 는 조용히 낡는다. 정말 새로 만들려면: --db {p}"
        )


def 쓸_DB(명시: str | os.PathLike[str] | None = None) -> Path:
    """**쓰기 진입점이 락을 잡기 전에 부르는 것.** 경로를 확정하고 유령 방지를 통과시킨다.

    ⚠️ 순서가 요점이다 — `connect()` 는 파일을 만들고 `PRAGMA journal_mode=WAL`(파일에
    남는 설정)까지 건다. 그걸 락 **전에** 하면, 물러날 실행도 DB 를 건드린 뒤에 물러난다.
    그래서 진입점은 `쓸_DB` → `락` → `connect` 순이다.

    ⚠️ 락이 부모 디렉터리를 만들므로 유령 방지가 **락보다 앞**이어야 한다. 뒤에 두면
    환경변수가 오타 난 날 없는 경로에 디렉터리만 남기고 거부한다.
    """
    p = db_path(명시)
    _유령방지(p, 명시)
    return p


# ── 단일 실행 락 ─────────────────────────────────────────────────────────────
#
# ⚠️ **락이 DB 를 아는 이 파일에 산다.** 종전에는 `collect.py` 안에 있어서, `bills.py`
#    `meetings.py` `members.py` `db.py --이행` 을 손으로 돌리면 **락을 아예 안 잡았다.**
#    예약 수집이 도는 중에 그중 하나를 돌리면 두 프로세스가 같은 DB 에 동시에 쓴다.


class 락:
    """이미 도는 실행이 있으면 **조용히 물러난다.** 죽이거나 락을 깨지 않는다 —
    따라잡기 설계라 선점해서 얻을 것이 없다.

    ⚠️ **끼는 락은 있어선 안 된다.** 물러날 때 종료코드가 0이면 락이 한 번 끼는 순간
    **워크플로가 매일 초록인 채 수집이 영원히 멈춘다** — 아무도 모른다. 그래서 판정을
    파일 내용이 아니라 **커널에 맡긴다**(`flock`). 커널은 프로세스가 어떻게 죽든
    (SIGKILL·OOM·재부팅) 락을 놓으므로 주인 없는 락이 원리적으로 생기지 않는다.

    ⚠️ **PID 를 읽어 `os.kill(pid, 0)` 로 판정하지 마라.** 국회가 실제로 그 방식이었고
    실측(2026-08-28)으로 둘 다 깨졌다 — 죽은 실행의 PID 를 무관한 프로세스가 물려받으면
    영원히 물러나고, `exists()` 와 쓰기 사이가 열려 있어 **동시에 들어온 12개가 전부
    락을 잡았다.** 법령 쪽 주석이 같은 것을 이미 못박아 두었는데도 국회에 그 검사가
    없어서 한쪽만 고쳐진 채 남아 있었다 — 계약은 `tests/락계약.py` 에 함께 둔다.

    파일은 지우지 않는다. 지우는 순간 '내가 연 fd 와 지금 파일이 같은 것인가'를 따져야
    하고, 락은 fd 를 닫으면 어차피 풀린다. 내용은 사람이 읽을 진단 기록일 뿐 판정에
    쓰지 않는다.
    """

    def __init__(self, db: str | os.PathLike[str] | None = None):
        self.path = Path(str(db_path(db)) + ".lock")
        self.잡음 = False
        self._fd: int | None = None

    def __enter__(self) -> 락:
        import errno
        import fcntl

        # 락 파일은 DB 옆에 산다. `--db` 로 새 경로를 처음 만들 때 여기가 먼저 오므로
        # 부모를 만들어 준다 — 안 그러면 `db.py init --db 새/경로.db` 가 죽는다.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(self._fd)
            self._fd = None
            # ⚠️ **`OSError` 전부를 「남이 쥐고 있다」로 읽으면 안 된다.** 비차단 `flock` 이
            #    '이미 잡혀 있다'로 주는 것은 EAGAIN/EWOULDBLOCK 뿐이고, ENOLCK·ENOTSUP·EIO
            #    는 **락 장치 자체가 고장 났다**는 뜻이다. 그걸 물러남으로 바꾸면 종료코드
            #    3 → 워크플로 초록이 되어, 락이 안 되는 파일시스템에서 **수집이 매일 조용히
            #    안 도는 채로 초록**이다. 고장은 시끄럽게 끝나야 한다.
            if e.errno not in (errno.EAGAIN, errno.EWOULDBLOCK):
                raise
            print(f"이미 도는 실행이 있다 ({self.path.read_text().strip()}). 물러난다.",
                  file=sys.stderr)
            return self
        os.ftruncate(self._fd, 0)
        os.write(self._fd, f"pid {os.getpid()} · {datetime.now(KST).isoformat()}\n"
                 .encode())
        self.잡음 = True
        return self

    def __exit__(self, *exc) -> None:
        if self._fd is not None:
            os.close(self._fd)   # 닫으면 커널이 락을 놓는다
            self._fd = None


@contextmanager
def 트랜잭션(conn: sqlite3.Connection):
    """진짜 트랜잭션. **`with conn:` 을 쓰지 마라.**

    ⚠️ `connect()` 가 `isolation_level=None`(autocommit)로 열기 때문에 `with conn:` 은
    **트랜잭션을 시작하지 않는다.** 파이썬 sqlite3 의 컨텍스트 매니저는 성공 시 `commit()`,
    실패 시 `rollback()` 을 부를 뿐인데, autocommit 에서는 열린 트랜잭션이 없어 **둘 다
    아무 일도 안 한다.** 즉 블록 중간에 터져도 **이미 쓴 것이 그대로 남는다.**

    ⚠️ 그 반쪽 상태가 조용한 이유는 다음 실행이 **부모 행이 있는지**로 건너뛸 대상을
    정하기 때문이다 — 회의는 들어갔는데 발언이 안 들어간 회의를 `SELECT 회의id FROM 회의`
    가 '이미 했다'로 읽는다. 발언 0행인 회의가 영구히 남고 아무 에러도 안 난다.

    ⚠️ **`BEGIN` 이 아니라 `BEGIN IMMEDIATE` 다.** 그냥 `BEGIN` 은 첫 쓰기까지 락을
    미루므로 두 실행이 나란히 읽고 나서 뒤늦게 하나가 `SQLITE_BUSY` 로 죽는다.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def _문장들(script: str) -> list[str]:
    """스크립트를 문장 단위로 자른다. **문자열·주석 안의 `;` 는 안 건드린다.**

    `sqlite3.complete_statement()` 가 SQLite 자신의 판정이라 직접 `;` 로 쪼개는 것과 다르다
    — 주석에 든 세미콜론이나 안 닫힌 따옴표를 우리가 다시 판정하지 않는다.
    """
    조각, 모음 = "", []
    for 줄 in script.splitlines(keepends=True):
        조각 += 줄
        if sqlite3.complete_statement(조각):
            모음.append(조각)
            조각 = ""
    if 조각.strip():
        모음.append(조각)
    return 모음


def _PRAGMA인가(문: str) -> bool:
    """앞의 빈 줄·`--` 주석을 지나 첫 낱말이 PRAGMA 인가."""
    for 줄 in 문.splitlines():
        줄 = 줄.strip()
        if 줄 and not 줄.startswith("--"):
            return 줄.upper().startswith("PRAGMA")
    return False


def _스키마적용(conn: sqlite3.Connection, script: str) -> None:
    """`SCHEMA` 를 **한 트랜잭션으로** 적용한다.

    ⚠️ **`executescript()` 를 쓰면 안 된다.** 문장 하나하나가 따로 커밋되므로,
    뷰의 `DROP VIEW IF EXISTS` → `CREATE VIEW` 쌍 사이에서 무엇이든 실패하면
    **뷰가 없는 DB 가 확정 커밋된다.** 1밀리초짜리 경합이 아니라 뒤쪽 문장 하나가
    깨지기만 해도 그렇게 되고, 그 상태는 조회에 `no such table` 로 나타나 다음 수집이
    돌 때까지 — 하루 — 이어진다.

    ⚠️ 감싸는 것만으로는 안 된다 — `executescript()` 는 **열린 트랜잭션도 암묵적으로
    커밋한다.** 그래서 문장 단위로 나눠 넣는다.

    ⚠️ **PRAGMA 는 트랜잭션 밖이다.** `journal_mode` 는 트랜잭션 안에서 바뀌지 않고,
    안에서 조용히 무시되면 **파일 속성이 안 바뀐 채로 초록**이 된다.
    """
    문장들 = _문장들(script)
    for 문 in 문장들:
        if _PRAGMA인가(문):
            conn.execute(문)
    with 트랜잭션(conn):
        for 문 in 문장들:
            if not _PRAGMA인가(문):
                conn.execute(문)


def init_schema(conn: sqlite3.Connection) -> None:
    """`CREATE TABLE IF NOT EXISTS` 를 전부 실행한다.

    ⚠️ **기존 DB 의 주석 변경은 이걸로 반영되지 않는다** — `IF NOT EXISTS` 라 테이블이 이미
    있으면 문장이 통째로 건너뛰어진다(실측 반영 0건). 주석을 고쳤으면 `migrate()` 다.
    """
    _스키마적용(conn, SCHEMA)


# ── 위원회 ────────────────────────────────────────────────────────────────────
# 표기 흔들림을 지우고 비교하는 데 쓰는 문자들. 원천이 같은 위원회를
# '기후위기 특별위원회' / '기후위기특별위원회' 로, 중점을 '·' / 'ㆍ' 로 섞어 쓴다.
#
# ⚠️ **파이썬과 SQL 이 반드시 이 한 리스트에서 나와야 한다.** 처음엔 파이썬만 정규식
#    `[\s·ㆍ・]` 로 썼는데, `\s` 는 유니코드 공백까지 지우고 SQL 의 REPLACE 목록은 안 지웠다.
#    그래서 '법제사법위원회 안건조정위원회　　　　(2026. 1. 7. 구성)' 에서
#    파이썬은 U+3000 을 지우고 SQL 은 남겨 **조회가 못 찾고 INSERT 로 빠져 UNIQUE 위반**이
#    났다. 리스트가 하나면 갈릴 수가 없다.
_지울문자 = (
    " ", "\t", "\n", "\r",
    " ",   # NO-BREAK SPACE
    "　",   # IDEOGRAPHIC SPACE — 위원회명에 실재한다
    "·", "ㆍ", "・",   # 가운뎃점 U+00B7 · 한글 U+318D · 가타카나 U+30FB
)

# SQLite 에 정규식이 없으므로 REPLACE 중첩으로 같은 집합을 만든다.
_정규화SQL = "위원회명"
for _c in _지울문자:
    _정규화SQL = f"REPLACE({_정규화SQL}, '{_c}', '')"


def _정규형(이름: str) -> str:
    for _c in _지울문자:
        이름 = 이름.replace(_c, "")
    return 이름


def upsert_위원회(
    conn: sqlite3.Connection, 이름: str, 상위: str | None = None
) -> str:
    """위원회를 넣고 **실제로 저장된 표기를 돌려준다.**

    ⚠️ **반환값을 써라. 넘긴 이름을 그대로 자식 행에 넣지 마라.** 두 원천이 같은 위원회를
    다르게 적으므로(`'기후위기 특별위원회'` / `'기후위기특별위원회'`) 나중에 온 표기로
    자식을 넣으면 FK 위반이거나, FK 가 꺼져 있으면 **조용한 고아 행**이다.

    보장하는 것은 '어느 표기가 옳은가'가 아니라 **하나로 모이는 것**이다 — 어느 쪽이 정식인지는
    원천에 근거가 없다. 먼저 저장된 것이 이긴다.

    `상위` 는 **원천이 알려줄 때만** 넘겨라. 이름을 쪼개서 만들지 마라(`위원회.상위위원회` 주석).
    """
    저장된 = conn.execute(
        f"SELECT 위원회명 FROM 위원회 WHERE {_정규화SQL} = ?", (_정규형(이름),)
    ).fetchone()
    이름 = 저장된[0] if 저장된 else 이름

    상위이름 = None
    if 상위:
        # 자기참조 FK 라 부모가 먼저다. 부모의 부모는 없다.
        상위이름 = upsert_위원회(conn, 상위)
        if 상위이름 == 이름:
            상위이름 = None  # 자기 자신을 상위로 두면 조회가 무한히 맴돈다

    if 저장된 is None:
        conn.execute(
            "INSERT INTO 위원회 (위원회명, 상위위원회) VALUES (?, ?)", (이름, 상위이름)
        )
    elif 상위이름:
        # ⚠️ **NULL 로 되돌리지 않는다.** 상위를 아는 원천과 모르는 원천이 번갈아 같은 행을
        #    쓰는데, 모르는 쪽이 나중에 오면 소위가 소위가 아니게 된다 —
        #    `상위위원회 IS NOT NULL` 이 "소위인가"의 유일한 답이라 소위 조회가 조용히 빈다.
        conn.execute(
            "UPDATE 위원회 SET 상위위원회 = ? WHERE 위원회명 = ?", (상위이름, 이름)
        )
    return 이름


# ── 의안 ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class 원천범위:
    """한 원천이 `의안` 에 쓸 수 있는 컬럼.

    **생성과 갱신이 다르다.** 갱신 범위는 `02-스키마.md` 의 "원천별 쓰기 범위" 표가 못박은
    것이고, 생성 범위는 그보다 넓을 수 있다 — `ALLBILL` 은 비법률안 525건의 **유일한**
    원천이라 새 행을 만들 수 있어야 하는데, `의안ID`·`의안명` 이 NOT NULL 이라 갱신 5컬럼
    만으로는 INSERT 자체가 불가능하다.
    """

    생성: tuple[str, ...]
    갱신: tuple[str, ...]


# ⚠️ **이 표를 어기면 매일 데이터가 지워진다.** `TVBPMBILL11` 이 매 실행 전량 UPSERT 되므로,
#    그 `SET` 에 남의 컬럼이 끼면 `ALLBILL`·`BPMBILLSUMMARY`·likms 가 채운 값이 **매일 아침
#    NULL 로 되돌아간다.** 그러면 `공포일 IS NOT NULL`("법이 됐나")이 항상 거짓이 되어
#    시나리오 1의 결말 판정이 통째로 무너지는데, **에러는 안 난다.**
#    `tests/test_db_의안.py` 가 이 표를 계획 문서와 대조한다(S2b).
의안_원천: dict[str, 원천범위] = {
    "TVBPMBILL11": 원천범위(
        # ⚠️ `의안종류` 는 응답 필드가 아니라 **상수 '법률안'** 이다. 그 API 는 법률안만 준다
        #    (결의안·예산안·동의안 전부 INFO-200). 이걸 ALLBILL 에만 맡기면 ALLBILL 을
        #    2,005건에만 부르므로 **계류 법률안 1만 4천 건의 의안종류가 영구히 NULL** 이 되고,
        #    재수집 조건 `의안종류 IS NULL`("아직 안 받은 비법률안")도 같이 무너진다.
        생성=("의안ID", "의안명", "의안종류", "제안자구분", "제안일", "소관위원회", "처리결과"),
        갱신=("의안ID", "의안명", "의안종류", "제안자구분", "제안일", "소관위원회", "처리결과"),
    ),
    "ALLBILL": 원천범위(
        # ⚠️ 생성에는 소관위원회가 있고 갱신에는 없다. `ALLBILL.JRCMIT_NM` 은 `GOV_` 행에서
        #    '본회의'를 주고(2200851 실측), 애초에 2,005건에만 부르므로 나머지 90%와 규칙이
        #    달라진다. 이 컬럼의 정본은 `TVBPMBILL11.CURR_COMMITTEE` 다 — 다만 비법률안
        #    525건은 그 API 에 아예 없어서, 새로 만들 때만 여기서 채운다.
        생성=(
            "의안ID", "의안명", "의안종류", "제안자구분", "제안일", "소관위원회",
            "처리결과", "정부이송일", "공포일", "공포법률명", "공포번호",
        ),
        갱신=("의안종류", "처리결과", "정부이송일", "공포일", "공포법률명", "공포번호"),
    ),
    # 아래 둘은 생성이 비어 있다 = **갱신 전용**이다. 의안이 이미 있다는 전제 위에서만 돌고,
    # 전제가 깨졌으면 그 자리에서 터진다. 0행 UPDATE 는 조용히 성공한 것처럼 보인다.
    "BPMBILLSUMMARY": 원천범위(생성=(), 갱신=("제안이유및주요내용",)),
    "anBillInfo.do": 원천범위(생성=(), 갱신=("대안의안번호",)),
}


def upsert_의안(conn: sqlite3.Connection, 원천: str, row: dict[str, object]) -> None:
    """`의안` 한 행을 그 원천이 쓸 수 있는 범위 안에서만 쓴다.

    `row` 에 `의안번호` 가 있어야 하고, 나머지 키는 그 원천의 범위 안이어야 한다.
    범위 밖 컬럼은 **조용히 무시하지 않고 거부한다** — 무시하면 "썼는데 반영이 안 된" 상태가
    되고, 그건 이 프로젝트에서 가장 찾기 어려운 종류의 오류다.

    `수집시각` 은 여기서 안 찍는다. 의안+의안심사 트랜잭션이 끝날 때 호출자가 찍는다.
    """
    if 원천 not in 의안_원천:
        raise ValueError(f"모르는 원천: {원천!r} (아는 것: {sorted(의안_원천)})")
    범위 = 의안_원천[원천]

    값 = dict(row)
    번호 = 값.pop("의안번호", None)
    if not 번호:
        raise ValueError("row 에 의안번호가 없다")

    허용 = set(범위.생성) | set(범위.갱신)
    if 밖 := set(값) - 허용:
        raise ValueError(f"{원천} 이 쓸 수 없는 컬럼: {sorted(밖)}")

    갱신값 = {k: v for k, v in 값.items() if k in 범위.갱신}
    이미있다 = conn.execute(
        "SELECT 1 FROM 의안 WHERE 의안번호=?", (번호,)
    ).fetchone() is not None

    # ⚠️ **존재 확인을 먼저 한다. `INSERT … ON CONFLICT DO UPDATE` 로 합칠 수 없다.**
    #    SQLite 는 충돌 해소보다 NOT NULL 검사를 먼저 하므로, 기존 의안에 ALLBILL 의
    #    갱신 5컬럼만 넘기면 `의안ID` 가 없다고 INSERT 단계에서 죽는다.
    if 이미있다:
        if 갱신값:
            conn.execute(
                f"UPDATE 의안 SET {', '.join(f'{k}=?' for k in 갱신값)} WHERE 의안번호=?",
                (*갱신값.values(), 번호),
            )
        return

    if not 범위.생성:
        raise LookupError(f"{원천}: 없는 의안 {번호}")

    생성값 = {k: v for k, v in 값.items() if k in 범위.생성}
    컬럼 = ["의안번호", *생성값]
    conn.execute(
        f"INSERT INTO 의안 ({', '.join(컬럼)}) VALUES ({', '.join('?' * len(컬럼))})",
        (번호, *생성값.values()),
    )


# ── 의안심사 ──────────────────────────────────────────────────────────────────
심사컬럼 = ("위원회명", "회부일", "상정일", "처리일", "처리결과")


def replace_의안심사(
    conn: sqlite3.Connection,
    의안번호: str,
    단계들: tuple[str, ...],
    rows,
) -> None:
    """의안 하나의 **지정한 단계만** 지우고 다시 넣는다.

    ⚠️ **UPSERT 를 쓰지 않는 이유.** `회차` 가 순서 기반이라 원천이 행을 재정렬하면 같은
    사실이 다른 회차를 받는다. 그러면 PK 가 안 부딪혀 UPSERT 가 덮어쓰기가 아니라
    **삽입**으로 동작해 에러 없이 행만 는다. 소위명이 52% 비어 있어 정렬 타이가 흔하다.

    ⚠️ **`단계들` 로 DELETE 를 좁히는 것이 핵심이다.** 원천 셋이 서로 다른 패스에서 이
    테이블을 쓴다 — `TVBPMBILL11`(소관위·법사위·본회의) · `ALLBILL`(재의) ·
    `TVBPMCONFINFO`(소위). 조건 없이 지우면 소위 패스가 앞의 것들을 날리는데,
    **A6 게이트는 본회의 행이 아예 사라진 경우를 검사하지 않아 초록불이다.**
    """
    rows = list(rows)
    if 밖 := {r["단계"] for r in rows} - set(단계들):
        raise ValueError(
            f"지우지 않는 단계를 넣으려 한다: {sorted(밖)} (지우는 단계: {sorted(단계들)}). "
            "그대로 두면 재실행마다 행이 는다."
        )

    conn.execute(
        f"DELETE FROM 의안심사 WHERE 의안번호=?"
        f" AND 단계 IN ({', '.join('?' * len(단계들))})",
        (의안번호, *단계들),
    )
    회차 = {}
    for r in rows:
        단계 = r["단계"]
        회차[단계] = 회차.get(단계, 0) + 1
        conn.execute(
            f"INSERT INTO 의안심사 (의안번호, 단계, 회차, {', '.join(심사컬럼)})"
            f" VALUES (?, ?, ?, {', '.join('?' * len(심사컬럼))})",
            (의안번호, 단계, 회차[단계], *(r.get(c) for c in 심사컬럼)),
        )


# ── 수집실패 원장 ─────────────────────────────────────────────────────────────
# ⚠️ **스키마의 CHECK 와 같은 목록이어야 한다.** 문서가 "수집실패에 남겨라"고 쓴 자리마다
#    대응하는 값이 없으면 그 INSERT 가 CHECK 위반으로 죽고, 의안/회의 하나의 트랜잭션이
#    통째로 롤백된다 — "부분 실패가 전체를 죽이지 않는다"가 그 자리에서 깨진다.
#    여기서 미리 거부하면 최소한 어느 값이 빠졌는지가 메시지에 남는다.
대상종류 = (
    "의안상세", "의안요약", "의안대안", "의안표결", "발의자",
    "회의본문", "회의의안", "발언의원", "의원",
)
실패종류 = ("재시도", "없음", "막힘", "보류")

# 몇 번 실패하면 큐에서 빼는가. **`audit.py` 의 A11 이 이 값으로 "포기한 항목"을 센다** —
# 큐가 이 값을 안 지키면 그 이름이 거짓이 되므로 정의가 원장을 소유한 여기에 있다.
재시도상한 = 5


def record_failure(
    conn: sqlite3.Connection,
    대상종류_: str,
    대상키: str,
    실패종류_: str,
    상세: str | None = None,
) -> None:
    """실패를 원장에 남긴다. **원장이 곧 재시도 큐다.**

    "이미 받았는가"만으로 할 일을 정하면, 한 번 성공한 뒤의 재수집 실패가 영영 재시도되지
    않는다 — 데이터는 남아 있으니 받은 것으로 보이는데 원장에는 실패가 있다.
    """
    if 대상종류_ not in 대상종류:
        raise ValueError(f"모르는 대상종류: {대상종류_!r} (아는 것: {sorted(대상종류)})")
    if 실패종류_ not in 실패종류:
        raise ValueError(f"모르는 실패종류: {실패종류_!r}")
    conn.execute(
        "INSERT INTO 수집실패 (대상종류, 대상키, 실패종류, 상세, 시도횟수, 마지막시도)"
        " VALUES (?, ?, ?, ?, 1, datetime('now','localtime'))"
        " ON CONFLICT(대상종류, 대상키) DO UPDATE SET"
        "   실패종류=excluded.실패종류, 상세=excluded.상세,"
        "   시도횟수=수집실패.시도횟수+1, 마지막시도=excluded.마지막시도",
        (대상종류_, 대상키, 실패종류_, 상세),
    )


def 재시도큐(conn: sqlite3.Connection, 대상종류_: str) -> set[str]:
    """다시 물어야 하는 대상키. **`record_failure` 가 약속한 그 큐를 실제로 읽는 자리다.**

    ⚠️ **"아직 데이터가 없는 것"으로 할 일을 정하면 그 약속이 조용히 깨진다.** 한 번 받은
    뒤의 재수집 실패는 데이터가 남아 있어 받은 것처럼 보이고, 원천의 열거에서 빠진 실패는
    후보에 오르지도 않는다. 둘 다 원장 행은 그대로인데 다시 요청되지 않으므로 **시도횟수가
    안 늘어 A11(재시도 상한)조차 영영 안 울린다** — `'막힘'` 도 아니라서 A4 도 안 가리킨다.
    수집도 없고 빨간불도 없는 정지 상태이고, 불변식 2 가 깨지는 가장 조용한 모양이다.

    `'없음'`·`'막힘'` 은 큐가 아니다. 전자는 다시 물어도 같은 답이고, 후자는 사람이 고칠
    일이라 요청을 늘리는 것이 도움이 안 된다. **상한에 닿은 것도 큐가 아니다** — 부르는
    쪽이 큐를 앞자리에 놓기 때문에, 영영 안 풀리는 항목을 남겨 두면 그것이 매 실행
    `--sample N` 을 통째로 차지해 **새 자료가 한 건도 안 들어온다.** 그 상태는 A11 이
    빨갛게 가리킨다.
    """
    if 대상종류_ not in 대상종류:
        # ⚠️ 오타는 조용히 빈 집합이 되고, **빈 집합은 "큐가 비었다"와 구별되지 않는다.**
        raise ValueError(f"모르는 대상종류: {대상종류_!r} (아는 것: {sorted(대상종류)})")
    return {
        r[0]
        for r in conn.execute(
            "SELECT 대상키 FROM 수집실패"
            " WHERE 대상종류=? AND 실패종류='재시도' AND 시도횟수 < ?",
            (대상종류_, 재시도상한),
        )
    }



def 포기한것(conn: sqlite3.Connection, 대상종류_: str) -> set[str]:
    """상한에 닿아 더는 묻지 않기로 한 대상키 — **`재시도큐` 의 여집합이다.**

    ⚠️ **큐에서만 상한을 지키면 우회로가 열린 채다.** 부르는 쪽은 큐 뒤에 "아직 안 받은
    것"을 붙이는데, 수집 실패는 데이터를 저장하기 전에 빠져나가 **행을 남기지 않는다** —
    그래서 상한에 닿아 큐에서 빠지는 순간 그 항목은 "아직 안 받은 것"의 조건을 그대로
    만족해 둘째 목록으로 되돌아온다. 시도횟수는 6·7·8 로 끝없이 오르고, A11 이 그것을
    "포기한 항목"이라 부르는 동안 수집기는 매 실행 같은 요청을 계속 던진다.

    **여기서 빠진 항목은 사람이 원장 행을 지워야 다시 큐에 든다.** 그 상태를 A11 이
    빨갛게 가리키므로 조용한 결손이 아니다 — 포기가 보이는 것이 이 설계의 값이다.

    ⚠️ **'보류' 도 여기 든다.** 사람이 확인한 포기(`보류()`)를 여기서 빼면 그 항목이
    둘째 목록으로 되돌아와, A11 이 조용한 채로 매 실행 같은 요청을 던진다.
    """
    if 대상종류_ not in 대상종류:
        raise ValueError(f"모르는 대상종류: {대상종류_!r} (아는 것: {sorted(대상종류)})")
    return {
        r[0]
        for r in conn.execute(
            "SELECT 대상키 FROM 수집실패"
            " WHERE 대상종류=? AND (실패종류='보류'"
            "   OR (실패종류='재시도' AND 시도횟수 >= ?))",
            (대상종류_, 재시도상한),
        )
    }


def 보류(conn: sqlite3.Connection, 대상종류_: str, 대상키: str, 이유: str) -> None:
    """상한에 닿은 포기를 **사람이 확인했다**고 표시한다. A11 이 더는 안 센다.

    ⚠️ **상한에 닿은 '재시도' 만 받는다.** 아직 큐에 있는 것을 사람이 먼저 포기시키면
    풀릴 수 있는 것이 영영 안 받아지고, '없음'·'막힘' 은 이미 다른 뜻이다.
    ⚠️ **이유를 원래 상세 뒤에 붙인다.** 지우면 왜 포기했는지(오배송인지 파싱 0건인지)가
    사라져 다음 사람이 처음부터 다시 잰다. 되돌리기는 `실패종류='재시도', 시도횟수=0` 이다.
    """
    if not 이유.strip():
        raise ValueError("보류에는 이유가 있어야 한다 — 다음 사람이 읽는다")
    행 = conn.execute(
        "SELECT 실패종류, 시도횟수 FROM 수집실패 WHERE 대상종류=? AND 대상키=?",
        (대상종류_, 대상키),
    ).fetchone()
    if 행 is None:
        raise ValueError(f"원장에 없다: {대상종류_} {대상키}")
    if 행[0] != "재시도" or 행[1] < 재시도상한:
        raise ValueError(
            f"상한에 닿은 '재시도' 만 보류할 수 있다: {대상종류_} {대상키} 는 {행[0]} {행[1]}회"
        )
    with 트랜잭션(conn):
        conn.execute(
            "UPDATE 수집실패 SET 실패종류='보류',"
            "  상세=COALESCE(상세,'') || ' · 보류(' || ? || '): ' || ?"
            " WHERE 대상종류=? AND 대상키=?",
            (_지금()[:10], 이유.strip(), 대상종류_, 대상키),
        )


def _지금() -> str:
    # ⚠️ SQLite 의 `datetime('now')` 는 UTC 다. 이 표의 시각을 그것과 섞어 비교하지 마라.
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")


def 상태기록(
    conn: sqlite3.Connection, 대상: str, 키: str, 상태: str,
    건수: int | None = None, 상세: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO 수집상태 (대상, 키, 상태, 건수, 상세, 갱신일시, 상태시작일시)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(대상, 키) DO UPDATE SET"
        "   상태=excluded.상태, 건수=excluded.건수, 상세=excluded.상세,"
        "   갱신일시=excluded.갱신일시,"
        # 상태가 그대로면 시작을 유지한다. 안 그러면 매 실행 덮여서 지속 기간이 사라진다.
        "   상태시작일시=CASE WHEN 수집상태.상태=excluded.상태"
        "     THEN COALESCE(수집상태.상태시작일시, 수집상태.갱신일시)"
        "     ELSE excluded.상태시작일시 END",
        (대상, 키, 상태, 건수, 상세, (때 := _지금()), 때),
    )


# 교체(DELETE 후 INSERT) 앞에서 원천을 얼마나 믿을지. **하나뿐인 손잡이다.**
#
# 10% 는 실측이 아니라 판단이다 — 일간 변동폭 이력이 아직 없다. 근거는 양쪽 여백이다:
# 위원 재배정은 (의원, 위원회) 짝을 **옮기지 지우지 않고** 현직 감소는 보궐 몇 명 단위라
# 정상 변동은 한 자릿수 %인 반면, 실제로 겪은 실패 모드는 빈 응답(100%)과 반토막(50%)이다.
# ⚠️ **틀려도 지우는 쪽으로 틀리지 않는다** — 막으면 옛 값이 남고 A12 가 빨개질 뿐이다.
#    그래서 좁히는 것보다 넓히는 것이 위험하다. 이력이 쌓이면 자료별로 다시 재라.
교체기준 = {"감소율상한": 0.10}


def 교체막는것(새것: int, 기존: int) -> str | None:
    """교체를 막을 이유. 없으면 `None`.

    ⚠️ **기존이 0이면 막지 않는다.** 첫 수집은 0 → N 이라 감소가 아니고, 여기서 막으면
    빈 DB 를 영영 못 채운다 — 가드가 스스로를 영구화한다.
    """
    if 새것 == 0:
        return "원천이 0행을 줬다"
    if 기존 and 새것 < 기존 * (1 - 교체기준["감소율상한"]):
        return f"{기존:,} → {새것:,} ({(1 - 새것 / 기존) * 100:.1f}% 감소)"
    return None


def clear_failure(
    conn: sqlite3.Connection, 대상종류_: str, 대상키: str, 실패종류_: str | None = None
) -> None:
    """성공했으니 지운다. **없어도 조용하다** — 성공 경로가 "원장에 있었나"를 먼저 묻지
    않아도 되게 하려는 것이다. 물어보게 만들면 언젠가 안 묻는 경로가 생긴다.

    ⚠️ **한 대상키를 두 경로가 건드리면 `실패종류_` 를 대라.** PK 가 (대상종류, 대상키)
    뿐이라 경로가 갈려도 행은 하나다 — 한쪽의 성공이 다른 쪽이 남긴 **'막힘' 을 지우면
    "사람이 고칠 때까지 감사가 가리킨다"는 계약이 조용히 깨진다.** 실측: 발의자가
    정확히 그렇다(전량 경로의 `등 N인` 불일치 vs 비법률안 보정 경로의 빈 응답).
    """
    if 실패종류_ is None:
        conn.execute(
            "DELETE FROM 수집실패 WHERE 대상종류=? AND 대상키=?", (대상종류_, 대상키)
        )
        return
    conn.execute(
        "DELETE FROM 수집실패 WHERE 대상종류=? AND 대상키=? AND 실패종류=?",
        (대상종류_, 대상키, 실패종류_),
    )


# ── 스키마 변경 ───────────────────────────────────────────────────────────────
# SQLite 는 `CREATE TABLE` 원문을 텍스트 그대로 `sqlite_master.sql` 에 보관하되
# `IF NOT EXISTS` 와 끝 세미콜론은 지운다(실측). 저장된 형태와 비교하려면 같게 만들어야 한다.
# ⚠️ 닫는 괄호를 **줄 맨 앞**에서만 찾으면 한 줄로 쓴 `CREATE TABLE` 이 통째로 빠진다.
# 빠진 테이블은 `migrate()`·`컬럼보강()` 의 사각지대이고 — 둘 다 조용히 건너뛴다 —
# 재구축에 넘기면 `KeyError` 로 죽는다. `$` 는 두 서식을 다 받는다.
_테이블문 = re.compile(r"^CREATE TABLE IF NOT EXISTS (\S+) \(.*?\n?\);$", re.S | re.M)


def _구조(sql: str) -> str:
    """주석과 공백 차이를 지운 비교용 형태.

    ⚠️ **괄호 안쪽 공백까지 지워야 한다.** `SCHEMA` 는 마지막 컬럼 뒤에 개행이 있어
    `수집시각 TEXT )` 인데 `ALTER TABLE` 이 다시 쓴 DDL 은 `수집시각 TEXT)` 다.
    이 한 칸 때문에 구조불일치로 판정되면 `migrate()` 가 물러나고, **컬럼을 손댄 날마다
    그 테이블의 주석이 DB 안에서 낡은 채로 남는다.**
    """
    조각, i, n = [], 0, len(sql)
    while i < n:
        c = sql[i]
        if c == "'":                       # ⚠️ 문자열 리터럴은 통째로 통과시킨다.
            j = i + 1                      #    안쪽 공백이 유의미하기 때문이다 —
            while j < n:                   #    `IN ('a, b')` 와 `IN ('a,b')` 는 다른 제약이다.
                if sql[j] == "'":
                    if j + 1 < n and sql[j + 1] == "'":
                        j += 2
                        continue
                    break
                j += 1
            조각.append(sql[i:j + 1]); i = j + 1; continue
        if c == "-" and i + 1 < n and sql[i + 1] == "-":
            k = sql.find("\n", i)
            i = n if k == -1 else k
            continue
        조각.append(c); i += 1
    밖 = "".join(조각)
    # 리터럴을 자리표시자로 빼 두고 정규화한 뒤 되돌린다.
    리터럴 = re.findall(r"'(?:[^']|'')*'", 밖)
    빈틀 = re.sub(r"'(?:[^']|'')*'", "\x00", 밖)
    빈틀 = re.sub(r"\s+", " ", 빈틀).strip()
    빈틀 = re.sub(r"\s*([(),])\s*", r"\1", 빈틀)
    for lit in 리터럴:
        빈틀 = 빈틀.replace("\x00", lit, 1)
    return 빈틀


def _저장형(문장: str) -> str:
    return 문장[:-1].replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ", 1)


def migrate(conn: sqlite3.Connection, schema: str | None = None, *,
            백업확인: bool = True) -> dict[str, list[str]]:
    """주석만 바뀐 테이블의 DDL 텍스트를 제자리에서 갈아 끼운다.

    `CREATE TABLE IF NOT EXISTS` 는 테이블이 이미 있으면 문장을 통째로 건너뛴다 —
    **기존 DB 에 주석 변경이 반영되지 않는다**(실측 0건). 이 DB 는 주석이 곧 문서라
    "주석만 고치는 변경"이 흔하므로 그 길이 필요하다.

    ⚠️ **구조가 다르면 손대지 않고 이름만 돌려준다.** `writable_schema` 는 검증 없이
    카탈로그를 갈아 끼우므로, 구조가 다른 DDL 을 넣으면 **테이블과 카탈로그가 어긋난 채로
    열린다.** 그 상태는 조용하다가 나중에 이상하게 터진다. 컬럼 추가·삭제·타입 변경은
    사람이 `ALTER TABLE` 로 하고 나서 이 함수를 부른다.

    ⚠️ **부르기 전에 DB 를 복사해 둬라.** `writable_schema` 로 망가진 DB 는 되돌릴 방법이 없다.
    """
    schema = SCHEMA if schema is None else schema
    결과: dict[str, list[str]] = {"주석교체": [], "구조불일치": []}
    바꿀것: list[tuple[str, str]] = []

    for m in _테이블문.finditer(schema):
        이름, 새 = m.group(1), _저장형(m.group(0))
        옛row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (이름,)
        ).fetchone()
        if 옛row is None or 옛row[0] == 새:
            continue
        if _구조(옛row[0]) != _구조(새):
            결과["구조불일치"].append(이름)
            continue
        결과["주석교체"].append(이름)
        바꿀것.append((새, 이름))

    if not 바꿀것:
        return 결과

    # ⚠️ **여기가 되돌릴 수 없는 지점이다.** `writable_schema` 로 카탈로그를 직접 고치므로
    #    잘못되면 되돌릴 방법이 없다 — 이 함수 주석이 처음부터 "부르기 전에 DB 를 복사해
    #    둬라"고 요구해 온 이유다. **주석 대신 문이 선다.**
    #    문이 `if not 바꿀것` **뒤에** 있는 것이 중요하다: 이 함수는 매 수집이 부르는데
    #    바꿀 것이 없으면 아무것도 안 하므로, 앞에 두면 **백업이 하루 밀린 날 수집 전체가
    #    멈춘다.** 문은 실제로 고칠 것이 있을 때만 선다.
    if 백업확인:
        _백업이_있어야_한다(conn)

    if (before := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
        raise RuntimeError(f"migrate 전부터 무결성이 깨져 있다: {before}")

    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("BEGIN")
    try:
        conn.execute("PRAGMA writable_schema=ON")
        conn.executemany(
            "UPDATE sqlite_master SET sql=? WHERE type='table' AND name=?", 바꿀것
        )
        # 카탈로그를 손으로 고쳤다는 것을 다른 연결이 알아채게 한다. 안 올리면 이미 열려 있는
        # 연결이 옛 스키마를 캐시한 채로 돈다.
        conn.execute(f"PRAGMA schema_version={version + 1}")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.execute("PRAGMA writable_schema=OFF")

    if (after := conn.execute("PRAGMA integrity_check").fetchone()[0]) != "ok":
        raise RuntimeError(f"migrate 뒤 무결성이 깨졌다: {after}")
    if 위반 := conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError(f"migrate 뒤 FK 위반: {위반}")
    return 결과


def _따옴표(값: str) -> str:
    return "'" + 값.replace("'", "''") + "'"


def _드리프트질의() -> str:
    """`SCHEMA` 와 `sqlite_master` 의 `CREATE TABLE` 텍스트가 갈렸는가 — 위반 테이블 수.

    ⚠️ **기대 목록에서 출발해 DB 쪽을 LEFT JOIN 한다.** 반대로 `sqlite_master` 에서
    출발하면 테이블이 하나도 없는 DB 가 모집단 0 으로 조용히 통과한다.

    ⚠️ **`_저장형()` 을 거친 텍스트와 비교한다.** SQLite 는 `IF NOT EXISTS` 와 끝의
    세미콜론을 떼고 저장하므로, 상수 원문을 그대로 비교하면 **항상** 빨갛다.
    """
    행 = ", ".join(
        "({}, {})".format(_따옴표(m.group(1)), _따옴표(_저장형(m.group(0))))
        for m in _테이블문.finditer(SCHEMA)
    )
    return (
        f"WITH 기대(이름, sql) AS (VALUES {행})"
        " SELECT COUNT(*) FROM 기대"
        " LEFT JOIN sqlite_master m ON m.type='table' AND m.name = 기대.이름"
        " WHERE m.sql IS NULL OR m.sql <> 기대.sql"
    )


드리프트질의 = _드리프트질의()


def 읽기연결(path: Path) -> tuple[sqlite3.Connection, str | None]:
    """조회용 연결 — 쓰지 않는다(`query_only`). 없는 경로는 여는 쪽이 먼저 거른다.

    ⚠️ **`mode=ro`·`-readonly` 를 기본으로 쓰지 마라.** WAL DB 는 `-shm` 이 없으면 읽기 전용
       열기가 `unable to open database file` 로 실패하는데, 아무것도 안 도는 평소가 정확히
       그 조건이다. 기본 열기 + `query_only=ON` 이 정확하면서 내용을 안 건드리는 길이다.

    ⚠️ **그 기본 열기도 읽기 전용 파일시스템(Codex 의 read-only 샌드박스)에서는 `-shm` 을
       못 만들어 같은 오류로 죽는다.** 그때만 `immutable=1` 로 물러난다 — WAL 을 통째로 무시해
       마지막 체크포인트 이전을 읽으므로 그 사실을 경고로 돌려준다. 둘째 값이 그 경고다.
    """
    try:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        return conn, None
    except sqlite3.OperationalError as e:
        # 실측 두 모양: Codex read-only 샌드박스는 `unable to open database file`, 쓰기 금지
        # 디렉터리의 쓰기 가능 파일은 `attempt to write a readonly database`(WAL 인덱스를 못 만든다).
        if "unable to open" not in str(e) and "readonly database" not in str(e):
            raise
    conn = sqlite3.connect(path.resolve().as_uri() + "?immutable=1", uri=True)
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn, ("⚠️ 읽기 전용 파일시스템이라 immutable 로 열었다 — 마지막 체크포인트 이후의"
                  " 적재(WAL)는 안 보인다.")


def 적재헤더(conn: sqlite3.Connection) -> str:
    """답의 기준시점과 마지막 감사 게이트 상태를 한 줄로 돌려준다."""
    시각 = conn.execute("SELECT MAX(수집시각) FROM 의안").fetchone()[0]
    적재 = f"적재 기준 {시각[:16]}" if 시각 else "적재 기록 없음"
    행 = conn.execute(
        "SELECT 상태, 건수, 상세, 갱신일시 FROM 수집상태"
        " WHERE 대상='감사' ORDER BY 갱신일시 DESC LIMIT 1"
    ).fetchone()
    감사 = "감사 기록 없음"
    if 행:
        상태, 건수, 상세, 갱신일시 = 행
        위반 = f"게이트 위반 {건수}건" if 건수 is not None else "게이트 위반 건수 기록 없음"
        감사 = f"감사 {갱신일시[:16]} {상태} {위반}"
        if 상세:
            감사 += f" ({상세})"
    return " ".join(f"{적재} · {감사}".split())


def 스키마출력(
    explicit: str | os.PathLike[str] | None = None, 이름들: tuple[str, ...] = (),
) -> tuple[str, str | None]:
    """헤더와 표·뷰 목록 또는 선택한 정의, 드리프트 경고를 함께 돌려준다.

    **DB 가 있으면 DB 것을 낸다.** 조회하는 쪽이 실제로 읽는 것이 `sqlite_master` 라,
    상수를 내면 "내가 본 스키마"와 "클로드가 보는 스키마"가 조용히 갈린다.

    ⚠️ **경고는 stdout 이 아니라 stderr 로 나가야 한다.** 이 명령의 출력은 그대로 읽히는
    스키마 본문이라, 경고 한 줄이 stdout 에 섞이면 그 줄이 스키마의 일부로 읽힌다.
    """
    순서 = re.findall(r"^CREATE (?:TABLE|VIEW) (?:IF NOT EXISTS )?(\w+)", SCHEMA, re.M)
    if 모름 := set(이름들) - set(순서):
        raise ValueError(f"모르는 이름: {', '.join(sorted(모름))}. 가능한 이름: {', '.join(순서)}")
    path = db_path(explicit)
    if not path.exists():
        return SCHEMA.strip(), f"⚠️ {path} 가 없어 **상수**를 냈다. 실물 DB 의 것이 아니다."
    conn, 폴백 = 읽기연결(path)
    try:
        정의 = dict(conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type IN ('table', 'view')"
        ))
        줄들 = [적재헤더(conn)]
        for 이름 in 순서:
            if 이름들 and 이름 not in 이름들:
                continue
            ddl = 정의.get(이름)
            if ddl is None:
                줄들.append(f"{이름}  없음  DB 에 정의 없음")
            elif 이름들:
                줄들.append(ddl + ";")
            else:
                주석 = re.search(r"--([^\n]*)", ddl)
                역할 = 주석.group(1).strip().replace("**", "") if 주석 else "역할 주석 없음"
                행수 = conn.execute(f'SELECT COUNT(*) FROM "{이름}"').fetchone()[0]
                # 컬럼 이름을 목록에 함께 낸다 — 정의를 읽기 전에 컬럼명을 짐작하는 습성이
                # `no such column` 의 원인이었다(e2e 2026-09-11). 뜻·함정은 정의 쪽에 있다.
                컬럼 = "·".join(r[1] for r in conn.execute(f'PRAGMA table_info("{이름}")'))
                줄들.append(f"{이름}  {행수}  {역할}  [{컬럼}]")
        본문 = "\n".join(줄들)
        갈림 = conn.execute(드리프트질의).fetchone()[0]
    finally:
        conn.close()
    경고 = 폴백
    if 갈림:
        경고 = ((경고 + "\n") if 경고 else "") + (
            f"⚠️ 테이블 {갈림}개가 상수와 다르다 — **DB 쪽이 낡았다.**"
            " `migrate` 를 돌려야 위 주석이 상수를 따라잡는다."
        )
    return 본문, 경고


def _컬럼삭제(conn: sqlite3.Connection, 로그, 목록: tuple[tuple[str, str], ...]) -> int:
    """⚠️ **인덱스가 걸린 컬럼은 못 지운다** — SQLite 가 `error in index … after drop
    column` 으로 거부한다. 지우려는 컬럼이 인덱스 밖인지 `sqlite_master` 로 먼저 확인해라.
    인덱스가 걸렸으면 `_테이블재구축` 쪽으로 보내는 것이 낫다(인덱스가 SCHEMA 대로 다시 만들어진다).
    """
    n = 0
    for 테이블, 컬럼 in 목록:
        if 컬럼 not in [r[1] for r in conn.execute(f"PRAGMA table_info({테이블})")]:
            continue  # 이미 없다 — 멱등
        conn.execute(f"ALTER TABLE {테이블} DROP COLUMN {컬럼}")
        로그(f"  컬럼 삭제 {테이블}.{컬럼}")
        n += 1
    return n


_인덱스명 = re.compile(r"CREATE\s+INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?(\S+)", re.I)


def _인덱스이름(ddl: str) -> str:
    m = _인덱스명.search(ddl)
    return m.group(1).strip('"') if m else ddl


def _인덱스문(테이블: str) -> list[str]:
    """`SCHEMA` 안에서 그 테이블에 걸린 `CREATE INDEX` 문장들."""
    return [
        m.group(0)
        for m in re.finditer(r"^CREATE INDEX[^\n]*?;", SCHEMA, re.M)
        if re.search(rf"\bON\s+{re.escape(테이블)}\s*\(", m.group(0))
    ]


def _재구축할것(conn: sqlite3.Connection, 테이블들: tuple[str, ...]) -> list[str]:
    """구조가 `SCHEMA` 와 다른 테이블만. **재구축과 백업 문이 같은 답을 봐야 한다** —
    문이 "고칠 것이 있나"를 따로 계산하면 둘이 갈리는 날 문이 헛되이 서거나 헛되이 열린다."""
    문장 = {m.group(1): m.group(0) for m in _테이블문.finditer(SCHEMA)}
    return [
        t for t in 테이블들
        if (옛 := conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (t,)
        ).fetchone()) and _구조(옛[0]) != _구조(_저장형(문장[t]))
    ]


def _테이블재구축(conn: sqlite3.Connection, 로그, 테이블들: tuple[str, ...]) -> int:
    """DDL 을 바꾸려고 테이블을 통째로 다시 만든다. **행을 흘리면 안 된다.**

    `ALTER TABLE` 로 못 하는 변경이 대상이다 — `CHECK` 추가, PK 변경, 컬럼 순서 변경.

    ⚠️ **판정은 컬럼 목록이 아니라 구조다.** 컬럼이 그대로인 채 제약만 붙는 변경
    (`CHECK`)이 있어서, 컬럼 차집합으로 물으면 **아무것도 안 하고 조용히 통과한다.**
    `_구조()` 는 주석과 공백만 지우므로 `CHECK` 은 남는다 — 그게 여기 쓰이는 이유다.

    ⚠️ **순서가 전부다.** 옛 테이블을 먼저 지워야 `CREATE INDEX IF NOT EXISTS` 가
    다시 만든다 — `ALTER TABLE RENAME` 은 인덱스를 옛 테이블에 붙인 채 이름만 남기므로,
    안 지운 상태에서 인덱스를 만들면 **이름이 이미 있다고 조용히 건너뛰고** 그 다음
    `DROP TABLE` 에 딸려 사라진다.

    ⚠️ **새 테이블은 `SCHEMA` 의 DDL 로 만든다.** `ALTER TABLE RENAME` 이 남기는
    `CREATE TABLE "이름"` 형태를 쓰면 따옴표 한 쌍 때문에 `migrate()` 가 영원히
    구조불일치로 물러나고, 그러면 이 DB 의 주석(=문서)이 안에서 낡은 채로 남는다.

    ⚠️ **`foreign_keys=OFF` · `legacy_alter_table=ON` 없이 부모를 재구축하면 자식이
    조용히 사라진다.** SQLite 3.25+ 는 `RENAME` 할 때 **다른 테이블의 `REFERENCES` 절을
    새 이름으로 고쳐 쓴다.** 그래서 `의안` → `_옛_의안` 으로 바꾸는 순간 `의안심사`·
    `발의자`·`표결집계`·`표결` 이 전부 `_옛_의안` 을 가리키게 되고, 그 다음
    `DROP TABLE _옛_의안` 이 **`ON DELETE CASCADE` 로 그 자식들을 데려간다.**

    가장 나쁜 것은 그 상태에서 **`PRAGMA foreign_key_check` 가 깨끗하다고 답한다는 것**
    이다 — 없는 테이블에는 걸 제약이 없어서다. 게이트가 거짓말을 하므로 막는 것 말고
    알아낼 방법이 없다. 두 PRAGMA 는 트랜잭션 안에서 무시되므로 밖에서 건다.

    ⚠️ **부르기 전에 DB 를 복사해 둬라.**
    """
    문장 = {m.group(1): m.group(0) for m in _테이블문.finditer(SCHEMA)}
    할것 = _재구축할것(conn, 테이블들)
    if not 할것:
        return 0  # 이미 목표 모양이다 — 멱등

    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        for 테이블 in 할것:
            산것 = [r[1] for r in conn.execute(f"PRAGMA table_info({테이블})")]
            목표 = re.findall(r"^\s{4}(\S+)\s+(?:TEXT|INTEGER|REAL|BLOB)\b", 문장[테이블], re.M)
            옮길 = ", ".join(c for c in 목표 if c in 산것)
            # ⚠️ **인덱스 DDL 을 지금 걷어 둔다.** `DROP TABLE` 이 그 테이블의 인덱스를
            #    함께 지우는데, `SCHEMA` 만 다시 돌리면 **운영자가 손으로 만든 인덱스는
            #    영영 사라진다** — 조회가 조용히 풀스캔이 되고 에러는 없다.
            인덱스 = [
                r[0] for r in conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='index'"
                    " AND tbl_name=? AND sql IS NOT NULL", (테이블,))
            ]
            conn.execute("BEGIN")
            try:
                전 = conn.execute(f"SELECT COUNT(*) FROM {테이블}").fetchone()[0]
                conn.execute(f"ALTER TABLE {테이블} RENAME TO _옛_{테이블}")
                # ⚠️ `executescript()` 는 열린 트랜잭션을 **암묵적으로 커밋한다.**
                #    여기서 쓰면 재구축이 원자성을 잃는다 — 한 문장이니 execute 로 충분하다.
                conn.execute(문장[테이블].replace("IF NOT EXISTS ", "", 1))
                conn.execute(f"INSERT INTO {테이블} ({옮길}) SELECT {옮길} FROM _옛_{테이블}")
                후 = conn.execute(f"SELECT COUNT(*) FROM {테이블}").fetchone()[0]
                if 전 != 후:
                    raise RuntimeError(f"{테이블} 재구축에서 행이 샜다: {전:,} → {후:,}")
                conn.execute(f"DROP TABLE _옛_{테이블}")
                # ⚠️ **같은 트랜잭션 안이어야 한다.** 커밋을 먼저 하고 루프가 끝난 뒤에
                #    되살리면, 그 사이 무엇이든 터졌을 때 **테이블은 확정됐고 인덱스만 없는
                #    반쪽 상태**가 남는다. 다시 돌려도 구조가 같아 아무것도 안 한다.
                #    `SCHEMA` 것이 정본이고, 그 밖의 것(운영자가 손으로 만든 것)은
                #    **최선노력**이다 — 재구축이 지운 컬럼을 가리키면 살릴 수 없다.
                for ddl in _인덱스문(테이블):
                    conn.execute(ddl)
                정본 = {_인덱스이름(d) for d in _인덱스문(테이블)}
                for ddl in (d for d in 인덱스 if _인덱스이름(d) not in 정본):
                    try:
                        conn.execute(ddl)
                    except sqlite3.OperationalError as e:
                        로그(f"  ⚠️ SCHEMA 밖 인덱스를 못 살렸다: {_인덱스이름(ddl)} — {e}")
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
            로그(f"  재구축 {테이블} {후:,}행")
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")

    # 켠 뒤에 본다 — 자식이 옛 이름을 물고 있는 상황은 위 PRAGMA 가 막는다.
    # 이건 옮기다 흘린 실제 고아를 잡는 그물이다.
    if 위반 := conn.execute("PRAGMA foreign_key_check").fetchall():
        raise RuntimeError(f"재구축 뒤 FK 위반: {위반[:20]}")
    return len(할것)


def 이행(conn: sqlite3.Connection, 로그=lambda _: None, *,
         백업확인: bool = True) -> dict[str, int]:
    """`SCHEMA` 와 **구조**가 다른 테이블을 다시 만든다. **멱등이다.**

    ⚠️ **`init_schema()` 다음, `migrate()` 앞에 부른다.** 뒤여야 구조를 다 옮긴 상태에서
    주석이 갈아 끼워지고, 앞이면 `migrate()` 가 구조불일치로 물러나 **주석이 낡은 채로 남는다.**

    ⚠️ **수집이 자동으로 부르지 않는다.** law 는 `스키마버전` 으로 게이트하지만 congress 에는
    그 표가 없어서, 자동으로 돌면 `SCHEMA` 에서 컬럼을 지운 날 **그 컬럼의 데이터가 조용히
    사라진다**(재구축은 양쪽에 다 있는 컬럼만 옮긴다). 사람이 `db.py 이행` 으로 부른다.

    ⚠️ **주석으로 백업을 요구하던 자리다. 이제 문이 대신 선다** — `백업확인=True` 면
    신선한 검증본이 없을 때 아예 시작하지 않는다. 재구축은 양쪽에 다 있는 컬럼만 옮기므로
    되돌릴 방법이 없고, 주석은 아무것도 막지 않는다.
    """
    전체 = tuple(m.group(1) for m in _테이블문.finditer(SCHEMA))
    # ⚠️ **문은 실제로 고칠 것이 있을 때만 선다.** 앞에 두면, 마지막 테이블을 재구축한
    #    직후 죽어 백업이 24시간을 넘긴 상황에서 **이미 목표 구조라 즉시 끝날 재실행이
    #    문에 막힌다** — 문이 불변식 2(중단돼도 이어받는다)를 깨뜨린다.
    if 백업확인 and _재구축할것(conn, 전체):
        _백업이_있어야_한다(conn)
    return {"재구축": _테이블재구축(conn, 로그, 전체)}


def _백업모듈():
    """`Scripts/backup.py`. **판정 규칙을 여기 옮겨 적지 않는다** — 두 곳에 적으면 한쪽만
    고치게 되고, 그 순간 문이 열린 채로 닫혀 있다고 믿게 된다.

    임포트가 함수 안에 있는 이유: `backup.py` 가 거꾸로 이 모듈을 불러 `db_path` 를 쓰므로,
    모듈 최상단에서 서로를 부르면 순환이 된다. 양쪽 다 **쓸 때** 부르면 순환이 아니다.
    """
    import importlib.util

    if (있음 := sys.modules.get("backup")) is not None:
        return 있음
    경로 = SKILL_DIR / "Scripts" / "backup.py"
    if not 경로.is_file():
        raise SystemExit(f"🔴 거부한다 — 백업 도구를 찾지 못했다: {경로}")
    spec = importlib.util.spec_from_file_location("backup", 경로)
    m = importlib.util.module_from_spec(spec)
    sys.modules["backup"] = m
    spec.loader.exec_module(m)
    return m


def _백업이_있어야_한다(conn: sqlite3.Connection) -> None:
    """**연결이 실제로 연 파일**을 물어 그 파일의 백업을 요구한다. 환경변수나 인자가
    아니라 연결에 묻는 이유는, 문이 지키는 대상과 고쳐지는 대상이 갈릴 수 없게 하려는 것이다."""
    m = _백업모듈()
    m.이행전_확인("congress", m.연파일(conn))


def failure_count(conn: sqlite3.Connection, 실패종류: str | None = None) -> int:
    if 실패종류:
        return conn.execute(
            "SELECT COUNT(*) FROM 수집실패 WHERE 실패종류=?", (실패종류,)
        ).fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM 수집실패").fetchone()[0]


def _main(argv: list[str] | None = None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    db도움 = f"DB 파일 경로. 기본값: ${DB_ENV} 또는 {{skill_dir}}/DBs/CONGRESS.db"
    ap.add_argument("--db", help=db도움)
    명령들 = ap.add_subparsers(dest="command", required=True)
    schema = 명령들.add_parser(
        "schema",
        description="국회 DB 의 표·뷰 목록과 정의를 읽는 도구다. SQL 을 만들 때 표를 찾고 컬럼과 주석을 확인한다.",
        epilog="첫 줄의 적재 기준은 의안의 마지막 수집 시각이며, 감사는 마지막 감사 상태와 게이트 위반 건수다.",
    )
    schema.add_argument("이름", nargs="*", help="표·뷰 이름: 없으면 목록, 있으면 그 정의")
    for 명령 in (schema, *(명령들.add_parser(이름) for 이름 in ("init", "migrate", "이행"))):
        명령.add_argument("--db", default=argparse.SUPPRESS, help=db도움)
    a = ap.parse_args(argv)

    if a.command == "schema":
        try:
            본문, 경고 = 스키마출력(a.db, tuple(a.이름))
        except ValueError as e:
            print(e, file=sys.stderr)
            return 2
        print(본문)
        if 경고:
            print(경고, file=sys.stderr)
        return 0

    # ⚠️ **`schema` 를 뺀 셋은 전부 DB 를 고친다.** 손으로 돌리는 이 경로가 락 밖에
    #    있으면 예약 수집이 도는 중에 테이블 재구축이 겹칠 수 있고, 그건 되돌릴 수 없다.
    경로 = 쓸_DB(a.db)
    잠금 = 락(경로)
    with 잠금:
        if not 잠금.잡음:
            return 3
        return _쓰기명령(a)


def _쓰기명령(a) -> int:
    conn = connect(a.db)
    try:
        if a.command == "init":
            init_schema(conn)
            print(f"스키마 적용: {db_path(a.db)}")
            return 0

        if a.command == "이행":
            n = 이행(conn, 로그=print)["재구축"]
            print(f"재구축 {n}개" if n else "구조가 이미 SCHEMA 와 같다")
            return 0

        결과 = migrate(conn)
        for 이름 in 결과["주석교체"]:
            print(f"주석 교체  {이름}")
        for 이름 in 결과["구조불일치"]:
            print(f"구조 불일치 {이름}  ← 손대지 않았다. ALTER TABLE 은 사람이 한다")
        if not any(결과.values()):
            print("바뀐 것 없음")
        # 구조 불일치는 실패가 아니다 — 사람이 볼 신호다. 종료코드로 자동화를 멈추지 않는다.
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(_main())
