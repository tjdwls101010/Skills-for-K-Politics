"""한 건을 **하나의 트랜잭션으로** 넣는다.

⚠️ **본문과 그 자식 행이 따로 커밋되면 반쪽 상태가 남는다.** 조문 없는 법령이나 별표 없는 행정규칙이 생기는데, 재개 조건이 "본문 컬럼이 NULL 인 것"이라 **다음 실행이 그걸 완성된 것으로 보고 건너뛴다.**

⚠️ **자식은 지우고 다시 넣는다.** 개정으로 조문이 통째로 갈릴 수 있어서 부분 갱신은 옛 조문을 남긴다. 부모의 `ON DELETE CASCADE` 에 기대지 마라 — 부모를 지우는 것이 아니라 갱신하는 것이라 트리거되지 않는다.

 원장 표들이 왜 이렇게 나뉘어 있는지도 여기 적는다 — 스키마 주석은 조회자가 읽는 자리라 게이트 설계는 거기 있으면 안 된다.

- **본문 수신은 `수집상태` 에서 추적하지 않는다.** 본문의 진행은 "본문 테이블에 그 행이 있나"가 그대로 답이라 원장이 따로 있으면 두 벌이 갈린다.
"""

from __future__ import annotations

import sqlite3
from law.conn import now_kst, 트랜잭션


본문테이블: dict[str, tuple[str, str, tuple[tuple[str, str], ...]]] = {
    "법령": (
        "법령",
        "법령일련번호",
        (("조문", "법령일련번호"), ("조문요소", "법령일련번호"), ("부칙", "법령일련번호")),
    ),
    "판례": ("판례", "판례일련번호", ()),
    "헌재결정례": ("헌재결정례", "헌재결정례일련번호", ()),
    "행정심판례": ("행정심판례", "행정심판례일련번호", ()),
    "법령해석례": ("법령해석례", "법령해석례일련번호", ()),
    "행정규칙": ("행정규칙", "행정규칙일련번호", (("행정규칙조문", "행정규칙일련번호"),)),
}


_컬럼캐시: dict[tuple[int, str], tuple[str, ...]] = {}



def 컬럼들(conn: sqlite3.Connection, 테이블: str) -> tuple[str, ...]:
    키 = (id(conn), 테이블)
    if 키 not in _컬럼캐시:
        _컬럼캐시[키] = tuple(
            r[1] for r in conn.execute(f"PRAGMA table_info({테이블})").fetchall()
        )
        if not _컬럼캐시[키]:
            raise LookupError(f"그런 테이블이 없다: {테이블}")
    return _컬럼캐시[키]



def _삽입(conn: sqlite3.Connection, 테이블: str, 값: dict, 방식: str = "REPLACE") -> None:
    쓸것 = {k: v for k, v in 값.items() if k in 컬럼들(conn, 테이블)}
    if not 쓸것:
        raise ValueError(f"{테이블}: 쓸 컬럼이 하나도 없다 — 받은 키 {list(값)}")
    자리 = ", ".join("?" * len(쓸것))
    conn.execute(
        f"INSERT OR {방식} INTO {테이블} ({', '.join(쓸것)}) VALUES ({자리})",
        tuple(쓸것.values()),
    )



def 저장(
    conn: sqlite3.Connection,
    종류: str,
    값: dict,
    자식: dict[str, list[dict]] | None = None,
) -> None:
    테이블, 키컬럼, 자식정의 = 본문테이블[종류]
    값 = {**값, "수집일시": 값.get("수집일시") or now_kst()}
    부모키 = 값.get(키컬럼)
    if not 부모키:
        raise ValueError(f"{종류}: {키컬럼} 이 비어 있다")

    with 트랜잭션(conn):
        # 자식은 FK 방향의 역순으로 지운다(조문요소 → 조문).
        for 자식테이블, 부모컬럼 in reversed(자식정의):
            conn.execute(f"DELETE FROM {자식테이블} WHERE {부모컬럼}=?", (부모키,))
        _삽입(conn, 테이블, 값)
        for 자식테이블, 부모컬럼 in 자식정의:
            for 행 in (자식 or {}).get(자식테이블, []):
                _삽입(conn, 자식테이블, {부모컬럼: 부모키, **행})
        conn.execute(
            "DELETE FROM 수집실패 WHERE 자료종류=? AND 자료ID=?", (종류, 부모키)
        )



# ── 실패 원장 ────────────────────────────────────────────────
#
# ⚠️ **비어 있는 것이 정상 상태다.** 성공하면 행이 지워진다. 원장이 곧 재시도 큐다.

재시도상한 = 5



def record_failure(
    conn: sqlite3.Connection, 종류: str, 자료ID: str, 실패종류: str, 메시지: str = ""
) -> None:
    conn.execute(
        "INSERT INTO 수집실패 (자료종류, 자료ID, 실패종류, 메시지, 시도횟수, 최종시도일시)"
        " VALUES (?, ?, ?, ?, 1, ?)"
        " ON CONFLICT(자료종류, 자료ID) DO UPDATE SET"
        "   실패종류=excluded.실패종류, 메시지=excluded.메시지,"
        "   시도횟수=수집실패.시도횟수+1, 최종시도일시=excluded.최종시도일시",
        (종류, 자료ID, 실패종류, 메시지[:500], now_kst()),
    )



def clear_failure(conn: sqlite3.Connection, 종류: str, 자료ID: str) -> None:
    conn.execute("DELETE FROM 수집실패 WHERE 자료종류=? AND 자료ID=?", (종류, 자료ID))



def 잘못붙은없음(conn: sqlite3.Connection, 종류: str) -> set[str]:
    """**원천이 '없다'고 말한 적 없는데 '없음' 표지가 붙은 자료ID.**

    ⚠️ **앞을 막는 것만으로는 이미 잃은 것이 안 돌아온다.** `원천._보낸다` 가 본문 4xx 를
    `원천에없음` 으로 확정하던 동안 붙은 표지는 그 코드를 고쳐도 그대로 남고, '없음'은
    재시도 대상에서 **영구히** 빠지는 표지라(`없음인것`) 다음 실행도 그 자료를 안 묻는다.

    실측(2026-08-28) — '없음' 507건 중 **506건이 HTTP 4xx 에서 왔고 봉투에서 온 것은
    1건뿐**이었다. 그 506건은 전부 2026-08-14 한 실행에 찍혔고, 무작위 다섯을 지금 원천에
    물으니 **5/5 가 정상**이다. 원천에 멀쩡히 있는 자료였다.

    ⚠️ **`메시지` 로 가른다.** 표지가 어디서 왔는지를 우리 원장이 이미 적어 두었고
    (`…: HTTP 404` 대 `…: 일치하는 판례가 없습니다…`), 그것 말고는 둘을 가를 근거가 DB
    안에 없다. 원천의 답에서 온 표지는 **건드리지 않는다** — 진짜 부재까지 매일 다시
    물으면 수만 건을 헛되이 두드리게 되고, 그게 이 표지가 있는 이유다.

    ⚠️ **여기는 고를 뿐 지우지 않는다.** 지우는 것은 `수집루프` 가 **실제로 물을 대상을
    확정한 뒤**다 — A1 이 `본문 + 없음 = 목록` 을 보므로, 이번에 안 물을 것의 표지를
    걷으면 좌변만 줄어 **이번 실행이 고칠 수 없는 빨간불**이 된다. `--표본` 이 정확히
    그 경우다: 표지는 506건을 다 걷었는데 실제로 묻는 것은 앞의 N 건뿐이다.
    """
    return {
        r[0]
        for r in conn.execute(
            "SELECT 자료ID FROM 수집실패 WHERE 자료종류=? AND 실패종류='없음'"
            " AND 메시지 LIKE '%: HTTP 4%'", (종류,)
        )
    }



def 없음인것(conn: sqlite3.Connection, 종류: str) -> set[str]:
    """`실패종류='없음'` 으로 확정된 것. **재시도 대상에서 뺀다.**"""
    return {
        r[0]
        for r in conn.execute(
            "SELECT 자료ID FROM 수집실패 WHERE 자료종류=? AND 실패종류='없음'", (종류,)
        )
    }



def 지금() -> str:
    """`정리후보` 가 재는 시각. **호출 횟수가 아니라 경과 시간이다.**"""
    return now_kst()



def 철회기록(conn: sqlite3.Connection, 종류: str, 자료IDs) -> int:
    """원천이 거둔 것으로 확정해 표지를 단다. 부르는 쪽이 **본문 API 로 확인한 뒤에만** 부른다.

    ⚠️ **`수집실패` 도 함께 치운다.** 한 자료가 '없음'과 '철회' 양쪽에 남으면 A1 이 같은
    행을 두 번 기대해 **고칠 수 없는 빨간불**이 된다 — 둘 다 "원천에 없다"의 기록이라
    한쪽만 남기는 것이 맞고, 남길 쪽은 본문을 갖고 있는 이쪽이다.

    `원천철회일` 이 **별도 표가 아니라 본문 여섯 표의 마지막 컬럼**인 이유가 여기 있다 —
    `SELECT *` 에 딸려 나와야 `WHERE 사건번호=…` 한 줄로 찾은 행이 철회된 것임을 놓칠 수
    없다. 별도 표면 조인을 안 한 조회가 아무 표시 없이 정상 자료를 돌려준다. 그리고
    **목록에서 빠진 것만으로는 값을 안 쓴다** — 목록 결함과 실제 철회를 가르는 증거가
    본문 API 의 「없다」뿐이라, 부르는 쪽이 그걸 확인한 뒤에만 여기 온다.
    복귀는 `복귀본문`(`원천철회일=NULL`)이 지운다 — **본문이 다시 받아진 경우에만.**

    ⚠️ **맨 뒤 컬럼이라 `ALTER TABLE … DROP COLUMN` 으로는 못 지운다.** 지우고 나면 닫는
    괄호 앞에 주석만 남고 SQLite 가 그 DDL 을 다시 못 읽어 `incomplete input` 으로 죽는다
    (실측). 지울 일이 생기면 `_테이블재구축` 으로 통째로 다시 만든다.
    """
    테이블, 키컬럼, _ = 본문테이블[종류]
    날 = 지금()[:10]
    conn.executemany(
        f"UPDATE {테이블} SET 원천철회일=? WHERE {키컬럼}=?",
        [(날, k) for k in sorted(자료IDs)])
    conn.executemany("DELETE FROM 정리후보 WHERE 자료종류=? AND 자료ID=?",
                     [(종류, k) for k in sorted(자료IDs)])
    conn.executemany("DELETE FROM 수집실패 WHERE 자료종류=? AND 자료ID=?",
                     [(종류, k) for k in sorted(자료IDs)])
    return len(set(자료IDs))



def 철회된것(conn: sqlite3.Connection, 종류: str) -> set[str]:
    테이블, 키컬럼, _ = 본문테이블[종류]
    return {r[0] for r in conn.execute(
        f"SELECT {키컬럼} FROM {테이블} WHERE 원천철회일 IS NOT NULL")}



def 복귀지우기(conn: sqlite3.Connection, 종류: str, 목록, 완결: bool) -> int:
    """목록에 다시 나타난 후보의 연속 누락을 끊는다.

    ⚠️ **덜 받은 목록에서도 이건 한다.** "목록에 있다"는 존재의 증거라 완결과 무관하게
    유효하지만, "목록에 없다"는 부재의 증거가 아니라 완결일 때만 유효하다. 이 비대칭을
    놓치면 `정상(누락) → 부분(존재) → 정상(누락)` 이 연속 2회로 세져 **복귀가 증명된
    자료가 지워진다.** 세는 것은 **연속** 누락이다.

    ⚠️ **원장에서 지우느냐 0 으로 되돌리느냐는 기준선이 정한다.** A1 은 `본문 + 없음 =
    목록 + 계류` 를 보는데 여기서 `목록` 은 **마지막 완결 목록**이다. 완결 목록에서 복귀한
    것은 그 목록에 들어 있으니 계류에서 빼야 맞고, 덜 받은 목록에서 복귀한 것은 기준선이
    그것 없이 세어진 옛 값 그대로라 **계류가 계속 그만큼을 메워야 한다** — 지워 버리면
    복귀할 때마다 A1 이 빨개져서 "흔한 부분 수신은 초록" 이라는 A25 와의 분업이 깨진다.
    """
    돌아온것 = [
        (종류, r[0]) for r in conn.execute(
            "SELECT 자료ID FROM 정리후보 WHERE 자료종류=?", (종류,)).fetchall()
        if r[0] in 목록
    ]
    conn.executemany(
        "DELETE FROM 정리후보 WHERE 자료종류=? AND 자료ID=?" if 완결
        else "UPDATE 정리후보 SET 연속누락=0 WHERE 자료종류=? AND 자료ID=?",
        돌아온것)
    return len(돌아온것)



def 상태기록(
    conn: sqlite3.Connection, 종류: str, 단계: str, 키: str, 상태: str, 건수: int | None = None
) -> None:
    conn.execute(
        "INSERT INTO 수집상태 (자료종류, 단계, 키, 상태, 건수, 갱신일시, 상태시작일시)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(자료종류, 단계, 키) DO UPDATE SET"
        "   상태=excluded.상태, 건수=excluded.건수, 갱신일시=excluded.갱신일시,"
        # 상태가 그대로면 시작을 유지한다. 안 그러면 매 실행 덮여서 지속 기간이 사라진다.
        "   상태시작일시=CASE WHEN 수집상태.상태=excluded.상태"
        "     THEN COALESCE(수집상태.상태시작일시, 수집상태.갱신일시)"
        "     ELSE excluded.상태시작일시 END",
        (종류, 단계, str(키), 상태, 건수, (때 := now_kst()), 때),
    )



def 상태읽기(conn: sqlite3.Connection, 종류: str, 단계: str, 키: str) -> str | None:
    r = conn.execute(
        "SELECT 상태 FROM 수집상태 WHERE 자료종류=? AND 단계=? AND 키=?",
        (종류, 단계, str(키)),
    ).fetchone()
    return r[0] if r else None



def 메타읽기(conn: sqlite3.Connection, 키: str) -> str | None:
    r = conn.execute("SELECT 값 FROM 메타 WHERE 키=?", (키,)).fetchone()
    return r[0] if r else None



def 기준선기록(conn, 종류: str, 목록건수: int, 갱신: bool, 검증된목록: bool) -> None:
    """`수집상태` 의 목록 건수를 남긴다. **기준선을 언제 갈아 끼우느냐가 이 함수의 전부다.**

    ⚠️ **정리보다 뒤에서 불러야 한다.** 앞에 두면 이번 목록 건수가 새 기준선이 되고, 정리가
    그 기준선에 맞춰 지운 뒤 A1 이 맞아떨어진다 — **게이트가 자기가 지운 것을 자기가 갱신한
    기준으로 검사하게 된다.**

    ⚠️ **덜 받았거나 이상하다고 판정한 목록은 기준선이 될 수 없다.** 되면 다음 실행이 그
    낮은 값과 비교해 통과하고, 크기 가드가 **삭제를 하루 늦추는 것 이상을 못 한다.**
    그래도 받았다는 사실 자체는 남긴다 — 다른 키에 적어 기준선과 섞이지 않게 한다.
    """
    if 갱신 and 검증된목록:
        상태기록(conn, 종류, "목록", "전체", "완료", 목록건수)
    else:
        상태기록(conn, 종류, "목록", "최근수신",
                "부분" if not 검증된목록 else "이상", 목록건수)



def 수집끝냈다(conn: sqlite3.Connection) -> None:
    """한 번의 수집 실행이 끝났다고 `메타` 에 적는다.

    ⚠️ **행을 하나도 안 썼어도 적는다.** 이 값의 뜻은 "무엇이 새로 들어왔나"가 아니라
    **"우리가 마지막으로 원천을 확인한 때"**다. 밀린 것이 없어 조용히 끝난 실행과
    수집기가 며칠째 안 도는 상태는 전혀 다른 일인데, 행이 있을 때만 적으면 둘이
    똑같아 보인다. 행 단위 해상도는 각 테이블의 `수집일시` 가 갖고 있다.
    """
    메타쓰기(conn, "마지막수집일시", now_kst())
    conn.execute(
        "INSERT INTO 메타 (키, 값) VALUES ('최초수집일시', ?)"
        " ON CONFLICT(키) DO NOTHING", (now_kst(),)
    )



def 메타쓰기(conn: sqlite3.Connection, 키: str, 값: str) -> None:
    conn.execute(
        "INSERT INTO 메타 (키, 값) VALUES (?, ?)"
        " ON CONFLICT(키) DO UPDATE SET 값=excluded.값",
        (키, str(값)),
    )

