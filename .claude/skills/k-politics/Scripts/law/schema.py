"""**이 파일의 `SCHEMA` 가 스키마의 원본이다.** 이 DB 는 주석이 곧 문서라(SQLite 가 `CREATE TABLE` 괄호 안의 `--` 주석을 `sqlite_master.sql` 에 텍스트로 보존한다) 여기 적은 주석이 그대로 조회자에게 간다 — **컬럼을 늘리면서 주석을 안 쓰면 그 컬럼은 설명이 없다.** 사본을 따로 두지 않는 이유도 그것이다: 두 곳이 갈리면 어느 쪽이 맞는지 아무도 모른다.

**그래서 SQL 문자열 안팎이 독자의 경계다** — `SCHEMA` 안의 `--` 주석은 **조회자**가 읽고, 파이썬 주석과 docstring 은 **이 파일을 고치는 사람**이 읽는다. 파서의 유래·적재 순서· `ALTER TABLE` 의 함정·감사 게이트의 설계처럼 조회 결과를 안 바꾸는 것은 바깥에 적는다. 안에 두면 조회자가 매 질의마다 자기와 무관한 것을 읽고, 정작 함정은 그 사이에 묻힌다. 반대로 **조회 결과의 해석을 바꾸는 운영 상태**(`수집실패` 의 '없음'·'철회', `수집상태` 의 '건너뜀')는 운영의 산물이라도 안에 남는다.
"""

from __future__ import annotations

import re


# 성진: 헌재 병합사건 번호별 연결 표 후보, 번호 경계 파서와 원문 보존·역적재 비용을 검증할 때 전환한다.
# 성진: 헌재 주문 항목별 복수 결과·근거 구절 적재 후보, 부정 표현과 복합 주문을 검수할 수 있을 때 전환한다.
# 성진: 조문 전문 FTS5 후보, 대표 질의가 5초를 넘으면 추가한다.
SCHEMA = r'''
CREATE TABLE IF NOT EXISTS 법령 (
  -- 현행 법령 하나가 한 행이다. 판본이 아니라 법이 행이므로 같은 법이 두 번 세어지지 않는다.
  -- 담는 판본은 원천이 현행으로 분류한 것이다. 현행 판본이 없는 법(제정 후 시행 전 등)은 시행예정 분류 판본 중 시행일이 오늘 뒤인 가장 이른 것, 그것도 없으면 가장 늦은 것을 담는다.
  -- 이미 공포된 다음 판본이 있으면 시행예정일련번호가 가리키며 그 판본의 조문은 여기 없다. 개정 전후 대조는 `direct.py 신구법`, 과거 판본은 `direct.py 연혁본문`이다.
  -- 날짜는 'YYYY-MM-DD'다. 'YYYYMMDD'와 비교하면 에러 없이 틀린 범위가 나온다. 빈 컬럼은 원천이 주지 않은 것이다.
  법령ID       TEXT NOT NULL PRIMARY KEY,  -- 법 자체의 원천 식별자로 개정돼도 유지된다. `조문`·`위임`·`의율조문`이 이 키로 잇는다.
  법령일련번호 TEXT NOT NULL UNIQUE,  -- 담긴 판본의 원천 MST로 `direct.py`가 받는 손잡이다. 개정 판본마다 새로 발급된다.
  법령명       TEXT NOT NULL,
  법령약칭     TEXT,  -- 판례가 인용하는 약칭이다.
  법종구분     TEXT NOT NULL,  -- 예: '법률'·'대통령령'·'국토교통부령'. 시행령·시행규칙도 이 표의 행이며 법과의 관계는 `위임`에 있다.
  소관부처     TEXT,
  공포일자     TEXT,
  공포번호     TEXT,  -- 판례의 '법률 제N호로 개정' 인용과 이어지도록 앞자리 0을 보존한 문자열이다.
  시행일자     TEXT,  -- 이 판본에서 가장 늦게 시행되는 조항의 시행일이다. 오늘 뒤여도 나머지 조항은 시행 중일 수 있으며 조별 시행일은 `조문.시행일자`다.
  제개정구분   TEXT,  -- 예: '제정'·'일부개정'·'전부개정'·'타법개정'.
  개정문       TEXT,  -- 이 판본을 만든 개정의 개정문이다.
  제개정이유   TEXT,  -- 입법 취지다.
  시행예정일련번호 TEXT,  -- 공포됐으나 아직 시행 전인 다음 판본의 MST다. NULL은 그런 판본이 없다는 뜻이다.
  시행예정일   TEXT,  -- 그 판본의 시행일이다. 예정 판본이 여럿이면 가장 이른 것이다.
  수집일시     TEXT NOT NULL,  -- 이 행을 받은 로컬 시각이며 원천 수정시각이 아니다.
  위임수집판본 TEXT,  -- `위임`을 받았을 때의 MST다. NULL이거나 법령일련번호와 다르면 `위임` 0행은 위임 없음이 아니라 미수집이다.
  CHECK ((시행예정일련번호 IS NULL) = (시행예정일 IS NULL))
);
CREATE INDEX IF NOT EXISTS idx_법령_명   ON 법령(법령명);
CREATE INDEX IF NOT EXISTS idx_법령_부처 ON 법령(소관부처);

CREATE TABLE IF NOT EXISTS 조문 (
  -- 현행 법령의 조 하나가 한 행이다. 법령 조회의 진입점이며 목차는 편장절, 본문은 전문이다.
  -- 항·호·목은 전문 안에 원천 순서대로 펼쳐져 있다. 항 하나만 떼어 읽으면 앞 항을 가리키는 문맥이 끊기므로 조 단위로 읽는다.
  법령ID   TEXT NOT NULL REFERENCES 법령(법령ID) ON DELETE CASCADE,
  순서     INTEGER NOT NULL,  -- 원천 배열 순서다. 조 번호 순과 다를 수 있어 본문 순서 복원은 이것이다.
  조       INTEGER NOT NULL,
  가지     INTEGER NOT NULL DEFAULT 0,  -- 제7조와 제7조의2는 조=7을 공유하고 가지 0과 2로 갈린다. 조만 걸면 둘이 함께 온다.
  편장절   TEXT,  -- 이 조가 속한 편·장·절 제목을 '>'로 이은 경로다. NULL은 편·장·절 구분이 없는 법령이다.
  제목     TEXT,
  전문     TEXT NOT NULL,  -- 조 머리와 모든 항·호·목을 이어 붙인 본문이다.
  시행일자 TEXT,  -- 조 단위 시행일로 `법령.시행일자`와 다를 수 있다.
  변경여부 TEXT,  -- 원천 'Y'는 이 판본에서 바뀐 조의 후보 표지이며 효력 확정값이 아니다.
  PRIMARY KEY (법령ID, 순서)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_조문_좌표 ON 조문(법령ID, 조, 가지);

CREATE TABLE IF NOT EXISTS 위임 (
  -- 법 조문이 시행령·시행규칙에 위임한 관계 하나가 한 행이다. 조문에서 대상 규범으로 향하며 대상 본문은 `위임대상조문`에 있다.
  -- 다른 법을 끌어 쓴 인용 관계·자치법규·행정규칙 위임은 담지 않는다. 인용은 조문 전문의 「법령명」을 LIKE로 찾고 행정규칙은 `direct.py`다.
  -- ⚠ 위임구분 '시행령'에는 다른 대통령령이 섞인다. 자기 시행령만 필요하면 대상법령ID나 정확한 이름으로 좁힌다.
  법령ID     TEXT NOT NULL REFERENCES 법령(법령ID) ON DELETE CASCADE,
  순서       INTEGER NOT NULL,
  조         INTEGER NOT NULL,  -- 위임한 원 법령의 조다.
  가지       INTEGER NOT NULL DEFAULT 0,
  위임한자리 TEXT,  -- 원천 조항호목 문자열로 예: '제2조제6호나목'.
  위임구분   TEXT NOT NULL CHECK (위임구분 IN ('시행령','시행규칙')),
  대상제목   TEXT,  -- 대상 규범의 이름이다.
  대상법령ID TEXT,  -- 대상제목을 현행 법령명에 맞춘 결과다. NULL은 매칭 실패이며 위임 부재가 아니다.
  대상조     INTEGER,  -- NULL은 원천이 대상 조를 주지 않은 관계다.
  대상가지   INTEGER NOT NULL DEFAULT 0,
  근거문구   TEXT,
  PRIMARY KEY (법령ID, 순서)
);
CREATE INDEX IF NOT EXISTS idx_위임_대상 ON 위임(대상법령ID, 대상조, 대상가지);

CREATE TABLE IF NOT EXISTS 판례 (
  -- 대법원 출처에서 수집한 판례 하나가 한 행이다. 하급심도 원천이 선별한 범위에서 들어 있다.
  -- ⚠ 검색 결과 없음은 이 수집 범위와 조건에서 못 찾았다는 뜻이며 판례 부재가 아니다. 빈 컬럼은 원천이 주지 않은 것이다.
  판례ID   TEXT NOT NULL PRIMARY KEY,  -- 원천 일련번호이며 law.go.kr 링크의 손잡이다. `의율조문`·`인용판례`의 자료ID가 이 값이다.
  사건번호 TEXT NOT NULL,  -- 인용에 쓰는 공개 번호다. 같은 사건번호의 행이 여럿일 수 있어 조인 키가 아니다.
  사건명   TEXT,
  법원명   TEXT,  -- 정규화한 정식 명칭이다. 옛 법원과 후신은 합치지 않는다.
  선고일자 TEXT,
  형식     TEXT CHECK (형식 IN ('판결','결정','명령','심판')),  -- NULL은 원천 표기를 분류하지 못한 것이다.
  전원합의체 INTEGER NOT NULL DEFAULT 0 CHECK (전원합의체 IN (0,1)),  -- 1은 대법원 전원합의체 재판이다. 판례 변경의 표지로 본문에 그 말이 없어도 이 값이 근거다.
  사건종류 TEXT,  -- 예: '민사'·'형사'·'일반행정'·'세무'·'특허'·'가사'.
  판시사항 TEXT,  -- 쟁점별 법리 요약이다. 사실관계는 판례내용에 있다.
  판결요지 TEXT,
  참조조문 TEXT,  -- 쟁점별 참조 조문 원문으로 예: '[1] 민법 제109조 / [2] …'. 조 좌표로 푼 것이 `의율조문`이다. NULL은 본문에 조문이 없다는 뜻이 아니다.
  참조판례 TEXT,  -- 쟁점별 선례 인용 원문으로 푼 것이 `인용판례`다.
  판례내용 TEXT,  -- 전문이다.
  수집일시 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_판례_사건번호 ON 판례(사건번호);
CREATE INDEX IF NOT EXISTS idx_판례_선고일   ON 판례(선고일자);
CREATE INDEX IF NOT EXISTS idx_판례_법원     ON 판례(법원명, 선고일자);

CREATE TABLE IF NOT EXISTS 헌재결정례 (
  -- 헌법재판소 결정 하나가 한 행이다. 전문도 요지도 없는 미공개 결정은 담지 않으며 `수집실패`의 '본문없음'이 그 자리다.
  -- 심판 종류는 사건번호의 '헌마'·'헌바'·'헌가' 같은 접미다. 빈 컬럼은 원천이 주지 않은 것이다.
  헌재결정례ID TEXT NOT NULL PRIMARY KEY,  -- 원천 일련번호이며 `의율조문`·`인용판례`의 자료ID가 이 값이다.
  사건번호     TEXT NOT NULL,  -- ⚠ 병합사건은 쉼표로 여러 번호가 와서 등가 조건은 놓친다. 부분문자열로 찾고 경계를 확인한다.
  사건명       TEXT,
  종국일자     TEXT,  -- NULL은 미종결을 뜻하지 않는다.
  주문         TEXT,  -- 전문에서 추출한 원문이다. 각하·기각·위헌이 함께 올 수 있어 단일 결과 코드가 아니다. NULL은 추출 실패다.
  심판대상조문 TEXT,  -- 위헌 여부를 다툰 조문의 원천 표기다. NULL은 심판대상 부재가 아니다.
  참조조문     TEXT,
  참조판례     TEXT,
  판시사항     TEXT,
  결정요지     TEXT,
  전문         TEXT,
  수집일시     TEXT NOT NULL,
  CHECK (COALESCE(NULLIF(전문, ''), NULLIF(판시사항, ''), NULLIF(결정요지, '')) IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_헌재_사건번호 ON 헌재결정례(사건번호);
CREATE INDEX IF NOT EXISTS idx_헌재_종국일   ON 헌재결정례(종국일자);

CREATE TABLE IF NOT EXISTS 법령해석례 (
  -- 법제처 법령해석 하나가 한 행이다. 다투기 전의 정부 공식 해석이라 판례·헌재 결정과 같은 권위로 다루지 않는다. 빈 컬럼은 원천이 주지 않은 것이다.
  법령해석례ID TEXT NOT NULL PRIMARY KEY,  -- 원천 일련번호이며 `의율조문`의 자료ID가 이 값이다.
  안건번호     TEXT NOT NULL,  -- 예: '20-0370'. 대외 인용은 '법제처 20-0370'이다.
  안건명       TEXT NOT NULL,  -- 대표 형식은 '기관 - 질의(「법령명」 제N조 등 관련)'이다. '등 관련'은 열거 밖 조문도 관련될 수 있다는 뜻이다.
  질의기관     TEXT,
  해석일자     TEXT,
  등록일자     TEXT,
  질의요지     TEXT,
  회답         TEXT,  -- 해석 결론의 원문이다.
  이유         TEXT,
  수집일시     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_해석례_해석일 ON 법령해석례(해석일자);

CREATE TABLE IF NOT EXISTS 의율조문 (
  -- 판단의 참조 문자열에서 조 좌표 하나를 뽑은 것이 한 행이다. 같은 판단이 같은 조를 출처·쟁점·항호목마다 따로 참조하므로 판단 하나가 여러 행이다.
  -- ⚠ 판단 수는 (자료종류, 자료ID)로 센다. 판단 목록은 `조문판단`, 조별 집계는 `조문판단수`가 이미 접어 두었다.
  -- ⚠ 0행은 참조 없음이 아니다. 참조 필드 결측·구법 표기·파싱 실패가 모두 0행이다. 구법 조 번호는 현행으로 바꾸지 않는다.
  자료종류   TEXT NOT NULL CHECK (자료종류 IN ('판례','헌재결정례','법령해석례')),
  자료ID     TEXT NOT NULL,  -- 자료종류에 따라 판례ID·헌재결정례ID·법령해석례ID다.
  출처       TEXT NOT NULL CHECK (출처 IN ('참조조문','심판대상조문','안건명')),  -- 헌재의 심판대상조문은 위헌 여부를 다툰 조문이고 참조조문은 근거로 든 조문이다.
  쟁점번호   INTEGER,  -- 참조 문자열의 [1]·[2] 경계로 판시사항의 번호와 짝이다. NULL은 쟁점 표지 없음이다.
  법령명원문 TEXT NOT NULL,  -- 참조 문자열이 쓴 법령 표기 그대로다. 법령ID가 NULL일 때 무엇을 가리켰는지는 이것뿐이다.
  법령ID     TEXT,  -- 법령명원문을 현행 법령에 맞춘 결과다. NULL은 매칭 실패이며 구법·폐지법·약칭이 원인일 수 있다.
  부칙여부   INTEGER NOT NULL DEFAULT 0 CHECK (부칙여부 IN (0,1)),  -- 1은 부칙 조 참조다. 부칙은 담지 않으므로 `조문`과 잇지 않는다.
  조         INTEGER,  -- NULL은 조 번호를 못 뽑은 참조다.
  가지       INTEGER NOT NULL DEFAULT 0,
  항         INTEGER,  -- NULL은 항이 명시되지 않은 참조다.
  호         INTEGER,
  호가지     INTEGER NOT NULL DEFAULT 0,
  목         TEXT,
  원문조각   TEXT NOT NULL  -- 이 행을 만든 참조 문자열 조각이다.
);
CREATE INDEX IF NOT EXISTS idx_의율_조문 ON 의율조문(법령ID, 조, 가지);
CREATE INDEX IF NOT EXISTS idx_의율_자료 ON 의율조문(자료종류, 자료ID);
CREATE INDEX IF NOT EXISTS idx_의율_명   ON 의율조문(법령명원문);

CREATE TABLE IF NOT EXISTS 인용판례 (
  -- 판례·헌재결정례가 선례 하나를 인용한 것이 한 행이다. 심급 연결은 아니며 원심 정보는 판례내용에 있다.
  자료종류       TEXT NOT NULL CHECK (자료종류 IN ('판례','헌재결정례')),
  자료ID         TEXT NOT NULL,  -- 인용한 쪽의 판례ID 또는 헌재결정례ID다.
  쟁점번호       INTEGER,  -- NULL은 쟁점 표지 없음이다.
  피인용종류     TEXT CHECK (피인용종류 IN ('판례','헌재결정례')),  -- NULL은 어느 표의 선례인지 못 정한 인용이다.
  피인용법원     TEXT,  -- 예: '대법원'·'헌법재판소'.
  피인용선고일   TEXT,
  피인용사건번호 TEXT NOT NULL,
  피인용자료ID   TEXT,  -- 피인용종류에 따라 판례ID 또는 헌재결정례ID이며 사건번호 후보가 하나일 때만 잇는다. NULL은 미매칭·다중 후보·수집 범위 밖이다.
  원문조각       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_인용_피인용 ON 인용판례(피인용사건번호);
CREATE INDEX IF NOT EXISTS idx_인용_자료   ON 인용판례(자료종류, 자료ID);

CREATE TABLE IF NOT EXISTS 수집상태 (
  -- 자료종류·단계별 마지막 수집 상태 하나가 한 행이다. 실행 이력이 아니라 지금 상태다.
  -- ⚠ 정리 단계가 '건너뜀'이면 원천에서 사라진 자료가 아직 남아 있을 수 있다. `신선도`의 경고가 이를 문장으로 말한다.
  자료종류     TEXT NOT NULL,  -- 법령·판례·헌재결정례·법령해석례, 그리고 실행 전체에 대한 행의 '전체'다.
  단계         TEXT NOT NULL CHECK (단계 IN ('목록','정리','수집','감사')),  -- 수집·감사는 자료종류 '전체'의 행이다.
  상태         TEXT NOT NULL CHECK (상태 IN ('완료','부분','이상','건너뜀','기준선없음')),
  건수         INTEGER,  -- 목록은 받은 건수, 정리는 지운 건수, 감사는 위반 게이트 수다.
  기준선       INTEGER,  -- 목록 단계에서 마지막으로 완결 수신하고 급감이 아니었던 건수다. 부분 수신·급감은 이 값을 덮지 않는다.
  상세         TEXT,  -- 감사는 위반 게이트의 이름과 건수, 그 밖은 사유다.
  갱신일시     TEXT NOT NULL,
  상태시작일시 TEXT NOT NULL,  -- 같은 상태가 이어지면 보존되는 시작 시각이다. 갱신일시와 벌어져 있으면 그만큼 이 상태가 지속됐다.
  PRIMARY KEY (자료종류, 단계)
);

CREATE TABLE IF NOT EXISTS 수집실패 (
  -- 원천과 이 DB가 어긋난 자료 하나가 한 행이다. 여기 없다고 세상에 없는 것이 아니라는 경계를 말한다.
  -- 실패종류의 뜻은 CHECK 순서대로다. 다음 실행이 다시 받는 오류 · 목록에는 있으나 본문이 원천에 없음 · 본문이 비어 담지 않음 · 담겨 있으나 목록에서 사라져 정리 대기 · 담았다가 원천이 거둬들여 지움.
  자료종류   TEXT NOT NULL,
  자료ID     TEXT NOT NULL,  -- 담기는 표의 키다. 법령은 법령ID이고 어느 판본이 실패했는지는 메시지에 있다.
  실패종류   TEXT NOT NULL CHECK (실패종류 IN ('재시도','없음','본문없음','목록누락','철회')),
  메시지     TEXT,
  시도횟수   INTEGER NOT NULL DEFAULT 1,  -- '목록누락'은 연속 누락 횟수다. 실패종류가 바뀌면 다시 센다.
  최초일시   TEXT NOT NULL,
  최종일시   TEXT NOT NULL,
  PRIMARY KEY (자료종류, 자료ID)
);

DROP VIEW IF EXISTS 위임대상조문;
CREATE VIEW 위임대상조문 AS
  -- 법 조문 하나가 시행령·시행규칙에 위임한 관계 하나가 한 행이며 대상 조문 본문을 붙인다. 대상 미해결 관계도 남긴다.
  -- ⚠ 대상전문 NULL은 대상 법령 미매칭·조 미제공·조 부존재로 본문을 못 붙였다는 뜻이며 위임 부재가 아니다.
  SELECT 원.법령ID, 원.법령명, y.조, y.가지, y.위임한자리, y.위임구분, y.근거문구, y.대상제목,
         대.법령ID AS 대상법령ID, 대.법령명 AS 대상법령명, y.대상조, y.대상가지,
         j.제목 AS 대상조제목, j.전문 AS 대상전문
    FROM 위임 y
    JOIN 법령 원 ON 원.법령ID = y.법령ID
    LEFT JOIN 법령 대 ON 대.법령ID = y.대상법령ID
    LEFT JOIN 조문 j ON j.법령ID = 대.법령ID AND j.조 = y.대상조 AND j.가지 = y.대상가지;

DROP VIEW IF EXISTS 조문판단;
CREATE VIEW 조문판단 AS
  -- 조 하나를 다룬 판단 하나가 한 행이다. 판례·헌재결정·법제처해석을 한 모양(번호·기관·일자·요지)으로 붙여 자료종류별 조인을 대신한다.
  -- ⚠ 여러 조를 한꺼번에 세면 같은 판단이 조마다 반복된다. 판단 수는 (자료종류, 자료ID)로 센다.
  -- 항·호·쟁점은 접었다. 그 구조와 부칙 참조·미매칭 참조는 `의율조문`에 있다.
  SELECT DISTINCT y.법령ID, y.조, y.가지, y.자료종류, y.자료ID,
         p.사건번호 AS 번호, p.법원명 AS 기관, p.선고일자 AS 일자, p.판시사항 AS 요지
    FROM 의율조문 y JOIN 판례 p ON y.자료종류='판례' AND p.판례ID = y.자료ID
   WHERE y.법령ID IS NOT NULL AND y.조 IS NOT NULL AND y.부칙여부 = 0
  UNION ALL
  SELECT DISTINCT y.법령ID, y.조, y.가지, y.자료종류, y.자료ID,
         d.사건번호, '헌법재판소', d.종국일자, COALESCE(d.판시사항, d.결정요지)
    FROM 의율조문 y JOIN 헌재결정례 d ON y.자료종류='헌재결정례' AND d.헌재결정례ID = y.자료ID
   WHERE y.법령ID IS NOT NULL AND y.조 IS NOT NULL AND y.부칙여부 = 0
  UNION ALL
  SELECT DISTINCT y.법령ID, y.조, y.가지, y.자료종류, y.자료ID,
         h.안건번호, '법제처', h.해석일자, h.회답
    FROM 의율조문 y JOIN 법령해석례 h ON y.자료종류='법령해석례' AND h.법령해석례ID = y.자료ID
   WHERE y.법령ID IS NOT NULL AND y.조 IS NOT NULL AND y.부칙여부 = 0;

DROP VIEW IF EXISTS 조문판단수;
CREATE VIEW 조문판단수 AS
  -- 현행 조 하나가 한 행이며 그 조를 다룬 판단을 자료종류별로 센다. 0은 판단 부재가 아니라 매칭 부재다.
  -- 수는 `조문판단`의 목록 길이와 같다. 둘 다 (자료종류, 자료ID)로 접으므로 쟁점·항호목 중복이 양쪽에서 같이 사라진다.
  -- ⚠ 판단 표에 없는 자료ID를 가리키는 `의율조문` 행이 있으면 여기서만 세어진다. 그 고아는 감사가 0으로 지킨다.
  SELECT c.법령ID, l.법령명, c.조, c.가지, c.제목, c.순서,
         COUNT(DISTINCT CASE WHEN y.자료종류='판례'       THEN y.자료ID END) AS 판례수,
         COUNT(DISTINCT CASE WHEN y.자료종류='헌재결정례' THEN y.자료ID END) AS 헌재수,
         COUNT(DISTINCT CASE WHEN y.자료종류='법령해석례' THEN y.자료ID END) AS 해석수
    FROM 조문 c JOIN 법령 l USING (법령ID)
    LEFT JOIN 의율조문 y ON y.법령ID = c.법령ID AND y.조 = c.조 AND y.가지 = c.가지 AND y.부칙여부 = 0
   GROUP BY c.법령ID, c.순서;

DROP VIEW IF EXISTS 신선도;
CREATE VIEW 신선도 AS
  -- **이 답은 언제 기준인가.** 항상 한 행이다 — 빈 DB 에서도 값이 NULL 인 한 행이 온다.
  -- 사용자가 "지금 어떻게 되어 있나"를 물을 때 그 '지금'이 며칠 전인지 모르면 낡은 시점의 답을 오늘 것인 양 내놓게 된다. 그래서 시점을 묻는 질의는 여기서 끝난다.
  SELECT s.갱신일시 AS 적재기준시각,  -- 마지막 수집 실행이 원천을 받고 끝난 시각(KST). NULL = 받은 기록이 없다.
         CASE WHEN g.상태 IS NULL THEN NULL WHEN g.상태 = '완료' AND COALESCE(g.건수, 0) = 0 THEN 1 ELSE 0 END AS 감사통과,  -- 1 = 게이트를 다 통과. NULL = 감사 기록 없음. **0 = 위반이 남아 있었다** — 이 DB 를 근거로 답할 때는 감사상세의 게이트가 무엇을 재는지 확인하거나, 못 하면 그 한계를 답에 밝힌다.
         g.갱신일시 AS 감사시각,  -- ⚠ **그 감사가 언제 것인가.** 감사는 수집이 돌 때만 기록되므로 수집이 며칠 멈췄으면 이 시각도 그만큼 옛것이고, 적재기준시각과 벌어져 있으면 그 사이의 적재는 감사를 안 거쳤다.
         g.상세 AS 감사상세,  -- 위반한 게이트의 이름과 건수. NULL = 위반 없음 또는 기록 없음.
         (SELECT CASE WHEN COUNT(*) > 0 THEN '마지막 수집이 ' || group_concat(자료종류, '·') || ' 의 정리를 건너뛰었다 — 원천에서 사라진 자료가 남아 있을 수 있다.' END
            FROM 수집상태 WHERE 단계 = '정리' AND 상태 = '건너뜀') AS 경고  -- ⚠ **어느 부분이 옛 값인가**를 문장으로. NULL = 그런 부분 없음.
    FROM (SELECT 1)
    LEFT JOIN 수집상태 s ON s.자료종류 = '전체' AND s.단계 = '수집'
    LEFT JOIN 수집상태 g ON g.자료종류 = '전체' AND g.단계 = '감사';
'''




# ── 마이그레이션 ─────────────────────────────────────────────

# ⚠️ 닫는 괄호를 **줄 맨 앞**에서만 찾으면 한 줄로 쓴 `CREATE TABLE` 이 통째로 빠진다.
# 빠진 테이블은 `migrate()`·`컬럼보강()` 의 사각지대이고 — 둘 다 조용히 건너뛴다 —
# 재구축에 넘기면 `KeyError` 로 죽는다. `$` 는 두 서식을 다 받는다.
_테이블문 = re.compile(r"^CREATE TABLE IF NOT EXISTS (\S+) \(.*?\n?\);$", re.S | re.M)


def _구조(sql: str) -> str:
    """주석과 공백 차이를 지운 비교용 형태.

    ⚠️ **괄호 안쪽 공백까지 지워야 한다.** `SCHEMA` 는 마지막 컬럼 뒤에 개행이 있어
    `주문 TEXT )` 인데 `ALTER TABLE ADD COLUMN` 이 다시 쓴 DDL 은 `주문 TEXT)` 다.
    이 한 칸 때문에 구조불일치로 판정되면 `migrate()` 가 물러나고, **컬럼을 더한 날마다
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
    리터럴 = re.findall(r"'(?:[^']|'')*'", 밖)
    빈틀 = re.sub(r"'(?:[^']|'')*'", "\x00", 밖)
    빈틀 = re.sub(r"\s+", " ", 빈틀).strip()
    빈틀 = re.sub(r"\s*([(),])\s*", r"\1", 빈틀)
    for lit in 리터럴:
        빈틀 = 빈틀.replace("\x00", lit, 1)
    return 빈틀


def _저장형(문장: str) -> str:
    return 문장[:-1].replace("CREATE TABLE IF NOT EXISTS ", "CREATE TABLE ", 1)


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


