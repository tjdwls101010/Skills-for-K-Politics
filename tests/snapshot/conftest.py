"""스냅샷 테스트 공용 픽스처.

**원천은 각 수집기의 `init_schema` 로 만든다.** 스키마를 여기 손으로 적으면 스냅샷 테스트가
수집기의 실제 스키마가 아니라 그 사본을 재게 되고, 수집기가 컬럼을 늘린 날 초록으로 남는다 —
스냅샷 빌더가 잡아야 하는 것이 정확히 그 상황이다.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts"
sys.path.insert(0, str(SCRIPTS))

import snapshot  # noqa: E402
from congress import db as congress_db  # noqa: E402
from law import migrate as law_migrate  # noqa: E402


@pytest.fixture
def 원천국회(tmp_path):
    p = tmp_path / "국회.db"
    conn = sqlite3.connect(p)
    congress_db.init_schema(conn)
    conn.executescript("""
        INSERT INTO 위원회 VALUES ('정무위원회', NULL);
        INSERT INTO 의원 VALUES ('KBZ59750','김형연','조국혁신당','2026-09-11','비례대표',1);
        INSERT INTO 의원 VALUES ('AAA00001','홍길동','무소속',NULL,'서울 종로',0);
        INSERT INTO 의원위원회 VALUES ('KBZ59750','정무위원회');
        INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,제안일,소관위원회,공포법률명,수집시각)
          VALUES ('2200001','ID1','법률안','개인정보 보호법 일부개정법률안','의원','2026-06-01','정무위원회',NULL,'2026-09-11 01:00:00');
        INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,제안일,공포법률명,수집시각)
          VALUES ('2200002','ID2','법률안','도로법 일부개정법률안(대안)','위원장','2026-06-02','도로법','2026-09-11 02:00:00');
        INSERT INTO 의안 (의안번호,의안ID,의안종류,의안명,제안자구분,제안일,수집시각)
          VALUES ('2200003','ID3','법률안','우주항공진흥법안','의원','2026-06-03','2026-09-11 03:00:00');
        INSERT INTO 발의자 VALUES ('2200001','KBZ59750','대표발의');
        INSERT INTO 표결집계 VALUES ('2200002','2026-07-01',300,290,280,5,5);
        INSERT INTO 표결 VALUES ('2200002','KBZ59750','찬성');
        INSERT INTO 회의 VALUES (1001,'C1','상임위원회','정무위원회',420,1,'2026-06-20');
        INSERT INTO 발언 VALUES (1001,1,'김형연','위원','KBZ59750','공익신고자 보호 관련하여 질의드립니다.');
        INSERT INTO 발언 VALUES (1001,2,'아무개','증인',NULL,'답변드리겠습니다.');
        INSERT INTO 회의의안 VALUES (1001,'2200001');
        INSERT INTO 수집상태 VALUES ('의원현직','전체','완료',299,NULL,'2026-09-11 01:16:19','2026-08-28 18:25:22');
        INSERT INTO 수집상태 VALUES ('감사','전체','완료',1,'A11=1','2026-09-04 06:45:59','2026-08-29 11:14:21');
        INSERT INTO 수집실패 VALUES ('회의본문','2002','재시도','타임아웃',3,'2026-09-10 05:00:00');
    """)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def 원천법령(tmp_path):
    p = tmp_path / "법령.db"
    conn = sqlite3.connect(p)
    law_migrate.init_schema(conn)
    conn.executescript("""
        INSERT INTO 법령 (법령일련번호,법령ID,법령명,법령약칭,법종구분,소관부처,공포일자,시행일자,현행연혁코드,수집일시)
          VALUES ('100001','011111','개인정보 보호법',NULL,'법률','개인정보보호위원회','2026-01-01','2026-03-01','현행','2026-09-12 08:00:00');
        INSERT INTO 법령 (법령일련번호,법령ID,법령명,법령약칭,법종구분,소관부처,공포일자,시행일자,현행연혁코드,수집일시)
          VALUES ('100002','011222','도로법',NULL,'법률','국토교통부','2026-02-01','2026-04-01','현행','2026-09-12 08:00:00');
        INSERT INTO 법령 (법령일련번호,법령ID,법령명,법령약칭,법종구분,소관부처,공포일자,시행일자,현행연혁코드,수집일시)
          VALUES ('100003','011333','국제물류진흥지역 지정 및 육성에 관한 특별법',NULL,'법률','해양수산부','2026-04-08','2027-04-08','시행예정','2026-09-12 08:00:00');
        INSERT INTO 법령 (법령일련번호,법령ID,법령명,법령약칭,법종구분,소관부처,공포일자,시행일자,현행연혁코드,수집일시)
          VALUES ('100004','011444','개인정보 보호법 시행령',NULL,'대통령령','개인정보보호위원회','2026-01-05','2026-03-01','현행','2026-09-12 08:00:00');
        INSERT INTO 조문 (법령일련번호,조문키,조문번호,조문가지번호,조문제목,조문내용,전문,조문여부,순서)
          VALUES ('100001','34',34,0,'개인정보 유출 등의 통지·신고','제34조','제34조(개인정보 유출 등의 통지·신고) ① 개인정보처리자는 …','조문',1);
        INSERT INTO 조문 (법령일련번호,조문키,조문번호,조문가지번호,조문제목,조문내용,전문,조문여부,순서)
          VALUES ('100002','1',1,0,'목적','제1조','제1조(목적) 이 법은 …','조문',1);
        INSERT INTO 수집실패 VALUES ('판례','99999','없음','원천 404',1,'2026-09-12 08:10:00');
        INSERT INTO 메타 VALUES ('마지막수집일시','2026-09-12 08:25:15');
        INSERT INTO 메타 VALUES ('마지막감사','통과');
    """)
    conn.commit()
    conn.close()
    return p


@pytest.fixture
def 소관기관DB():
    """**실물을 쓴다.** 이 DB 는 레포에 커밋된 진실 원천이고 728행짜리 53KB 라 흉내 낼 이유가 없다."""
    return SCRIPTS.parent / "DBs" / "소관기관.db"


@pytest.fixture
def 빌드(tmp_path, 원천국회, 원천법령, 소관기관DB):
    출력 = tmp_path / "법.db"
    결과 = snapshot.build(원천국회, 원천법령, 소관기관DB, 출력)
    conn = sqlite3.connect(f"file:{출력}?mode=ro", uri=True)
    yield 결과, conn, 출력
    conn.close()
