#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx[http2]>=0.27"]
# ///
"""위원회 · 의원 · 의원위원회.

**FK 부모가 가장 먼저 선다.** `위원회` 는 `의안`·`의안심사`·`회의`·`의원위원회` 넷의 부모이고
`의원` 은 `발의자`·`표결`·`발언` 셋의 부모다. 이 파일이 수집 순서의 맨 앞인 이유다.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import db  # noqa: E402
import net  # noqa: E402

대수 = "제22대"

# 원천 — `01-원천-실측.md` §2-7, §3
위원회목록_API = "nkimylolanvseqagq"   # 회의록 대별 위원회 목록. TH=22, 166행
위원명단_API = "nktulghcadyhmiqxi"     # 위원회 위원 명단. 477행, MONA_CD 전 행
의원전체_API = "ALLNAMEMBER"           # 전 대수 3,295행
현직_API = "nwvrqwxyaytdsfvhu"         # 현직 299명


def parse_위원회목록(rows) -> list[tuple[str, str | None]]:
    """`nkimylolanvseqagq` 응답 → `[(위원회명, 상위위원회|None)]`, **상위가 먼저.**

    ⚠️ **`CLASS_NM` 을 쓰지 마라.** 같은 위원회가 `상임위원회` 로도 `국정감사` 로도 반복해서
    온다(법사위가 둘 다에 있다). 옛 프로젝트가 이 값을 위원회에 넣어 **상임위 17개 중 14개를
    '국정감사'로 굳혔다.** 회의의 성질은 `회의.회의종류` 다.

    ⚠️ **소위 이름을 공백으로 쪼개서 상위를 만들지 마라.** 여기서는 원천이 `CMIT_NM` 과
    `SUB_CMIT_NM` 을 **별도 필드로** 주므로 추측이 필요 없다.
    """
    상위들: list[str] = []
    소위들: list[tuple[str, str]] = []
    for r in rows:
        부모 = (r.get("CMIT_NM") or "").strip()
        if not 부모:
            continue
        if 부모 not in 상위들:
            상위들.append(부모)
        if 소위명 := (r.get("SUB_CMIT_NM") or "").strip():
            쌍 = (f"{부모} {소위명}", 부모)
            if 쌍 not in 소위들:
                소위들.append(쌍)
    # 부모를 전부 먼저 — 자기참조 FK 라 순서가 곧 제약이다.
    return [(이름, None) for 이름 in 상위들] + 소위들


def parse_member(row: dict) -> tuple[dict, list[str]]:
    """`ALLNAMEMBER` 한 행 → `({의원코드,이름,정당,선거구}, 미해결필드들)`.

    ⚠️ **`PLPT_NM`·`ELECD_NM` 은 대수별 이력이 `/` 로 이어져 온다.** 그대로 넣으면 정당명이
    `'미래통합당/국민의힘'` 이 된다. `GTELT_ERACO` 의 대수 목록과 **자리를 맞춰** 22대 값을 집는다.

    ⚠️ **조각 수가 대수 수와 다르면 채우지 않는다.** 어느 자리가 빠졌는지는 근거가 없고,
    추측하면 틀려도 에러가 안 난다. 실측: 320명 중 `ELECD_NM` 1명이 이 경우다.

    ⚠️ **`BLNG_CMIT_NM` 에는 이 규칙을 적용하지 마라.** 그건 `/` 이력이 아니라 쉼표 구분이다
    (22대 표본에 `/` 가 0명). 애초에 `의원위원회` 는 `nktulghcadyhmiqxi` 에서 온다.
    """
    대수목록 = [e.strip() for e in (row.get("GTELT_ERACO") or "").split(",") if e.strip()]
    if 대수 not in 대수목록:
        raise ValueError(f"{row.get('NAAS_CD')}: {대수} 가 GTELT_ERACO 에 없다 — 22대 명부가 아니다")
    자리 = 대수목록.index(대수)

    값: dict[str, object] = {
        "의원코드": row.get("NAAS_CD"),
        "이름": row.get("NAAS_NM"),
    }
    # 22대는 아직 마지막 대수다(23대가 없다). **그 경우 마지막 조각이 곧 22대 값이므로**
    # 조각 수가 어긋나도 자리를 맞출 필요가 없다 — 추측이 아니라 구조에서 나오는 사실이다.
    # 실측: 박지원(5선)이 대수 5개 / 선거구 조각 4개인데, 마지막 조각 '전남 해남군완도군진도군'
    # 이 실제 22대 선거구다. 이 규칙이 없으면 그 한 명이 영구히 빨간 게이트가 된다.
    끝자리 = 자리 == len(대수목록) - 1

    미해결: list[str] = []
    for 컬럼, 필드 in (("정당", "PLPT_NM"), ("선거구", "ELECD_NM")):
        조각 = [x.strip() for x in (row.get(필드) or "").split("/")]
        if len(조각) != len(대수목록) and 끝자리 and 조각:
            값[컬럼] = 조각[-1] or None
        elif len(조각) != len(대수목록):
            # 자리를 맞출 수 없다. **사람이 볼 값이다** — 실측 320명 중 1명(박지원 5선,
            # 대수 5개 / 선거구 조각 4개)이 이 경우다.
            값[컬럼] = None
            미해결.append(컬럼)
        else:
            # ⚠️ **빈 값은 미해결이 아니다.** 원천이 안 주는 것을 원장에 남기면 우리가 고칠
            #    수 없는 빨간불이 영구히 남고, **고칠 수 없는 빨간불은 표 전체를 무시하게
            #    만든다.** 실측 2명(손솔·최혁진)의 ELECD_NM 이 빈 문자열이다.
            값[컬럼] = 조각[자리] or None
    return 값, 미해결


def parse_현직(rows) -> dict[str, str]:
    """현직 API 응답 → `{의원코드: 현재 정당}`. 코드나 정당이 비면 제외한다."""
    현직 = {}
    for r in rows:
        코드 = (r.get("MONA_CD") or "").strip()
        정당 = (r.get("POLY_NM") or "").strip()
        if 코드 and 정당:
            현직[코드] = 정당
    return 현직


def parse_의원위원회(rows) -> list[tuple[str, str]]:
    """`nktulghcadyhmiqxi` 응답 → `[(의원코드, 위원회명)]`.

    ⚠️ **이름으로 잇지 마라 — 22대에 박지원이 둘이다.** 이 API 는 `MONA_CD` 를 직접 주므로
    (실측 477/477) 그 문제가 없다. `ALLNAMEMBER.BLNG_CMIT_NM` 에서 파생하면 이름으로
    되찾아야 한다.

    `JOB_RES_NM`(위원장·간사·위원)도 오지만 담지 않는다 — 세 시나리오 중 어디에도 안 걸린다.
    """
    쌍: list[tuple[str, str]] = []
    for r in rows:
        코드 = (r.get("MONA_CD") or "").strip()
        이름 = (r.get("DEPT_NM") or "").strip()
        if 코드 and 이름 and (코드, 이름) not in 쌍:
            쌍.append((코드, 이름))
    return 쌍


def collect(conn, c: net.Client, 로그=print) -> dict[str, int]:
    """위원회 → 의원 → 의원위원회 순으로 채운다. **매 실행 전량이다** (7요청, 몇 초)."""
    수치: dict[str, int] = {}

    # ── 위원회 ────────────────────────────────────────────────────────────────
    위원회행 = list(c.all_pages(위원회목록_API, TH=22))
    with db.트랜잭션(conn):
        저장명 = {}
        for 이름, 상위 in parse_위원회목록(위원회행):
            저장명[이름] = db.upsert_위원회(conn, 이름, 상위=상위)
    수치["위원회"] = conn.execute("SELECT COUNT(*) FROM 위원회").fetchone()[0]
    수치["소위"] = conn.execute(
        "SELECT COUNT(*) FROM 위원회 WHERE 상위위원회 IS NOT NULL"
    ).fetchone()[0]
    로그(f"위원회 {수치['위원회']}행 (소위 {수치['소위']})")

    # ── 의원 ──────────────────────────────────────────────────────────────────
    # 현직 판정의 근거는 하나뿐이다: 인적사항 API 에 있는가.
    현직행 = list(c.all_pages(현직_API))
    현직 = {r["MONA_CD"] for r in 현직행 if r.get("MONA_CD")}
    # 정당은 같은 응답의 `POLY_NM` 이다 — `ALLNAMEMBER.PLPT_NM` 은 대수별 값이라 22대 안에서
    # 옮긴 당적(위성정당 합당·탈당)을 모른다. 정당이 빈 행은 현직이되 정당은 옛 값을 지킨다.
    현직정당 = parse_현직(현직행)
    전체 = [r for r in c.all_pages(의원전체_API) if 대수 in (r.get("GTELT_ERACO") or "")]

    # ⚠️ **명단이 줄면 빠진 의원이 전부 `현직여부=0` 이 되는데 행수는 안 바뀐다.**
    #    컬럼 하나만 뒤집히는 사고라 행수 등식을 보는 게이트로는 구조적으로 안 잡힌다.
    # ⚠️ **막을 때 현직여부·정당·정당확인일 갱신을 함께 멈춘다.** 의원 표 전체를 멈추면 원천이 이상한
    #    하루가 이름·선거구까지 하루 낡게 만든다 — 그 컬럼들은 멀쩡히 왔는데도.
    #    막힌 동안 **처음 나타난 의원**은 `현직여부=0` 으로 들어간다(INSERT 경로에는 지킬
    #    옛 값이 없다). 추측해서 1을 넣지 않는 쪽을 택했고, 그 창은 A12 가 빨간 동안이다.
    현직막는것 = db.교체막는것(
        len(현직), conn.execute("SELECT COUNT(*) FROM 의원 WHERE 현직여부=1").fetchone()[0]
    )
    현직갱신 = "의원.현직여부" if 현직막는것 else "excluded.현직여부"

    미해결수 = 0
    with db.트랜잭션(conn):
        for row in 전체:
            값, 미해결 = parse_member(row)
            conn.execute(
                "INSERT INTO 의원 (의원코드, 이름, 정당, 선거구, 현직여부)"
                " VALUES (?,?,?,?,?)"
                " ON CONFLICT(의원코드) DO UPDATE SET"
                "   이름=excluded.이름,"
                f"   선거구=excluded.선거구, 현직여부={현직갱신}",
                (
                    값["의원코드"], 값["이름"], 값["정당"], 값["선거구"],
                    int(값["의원코드"] in 현직),
                ),
            )
            if 미해결:
                미해결수 += 1
                # '막힘' 이다 — 다시 받아 봐야 같은 값이 오고, 사람이 규칙을 고쳐야 풀린다.
                db.record_failure(
                    conn, "의원", str(값["의원코드"]),
                    "막힘", f"대수이력 자리 불일치: {','.join(미해결)}",
                )
            else:
                db.clear_failure(conn, "의원", str(값["의원코드"]))
        if not 현직막는것:
            오늘 = datetime.now(db.KST).strftime("%Y-%m-%d")
            conn.executemany(
                "UPDATE 의원 SET 정당=?, 정당확인일=? WHERE 의원코드=?",
                [(정당, 오늘, 코드) for 코드, 정당 in 현직정당.items()],
            )
        db.상태기록(
            conn, "의원현직", "전체",
            "건너뜀" if 현직막는것 else "완료", len(현직), 현직막는것,
        )
    수치["의원"] = conn.execute("SELECT COUNT(*) FROM 의원").fetchone()[0]
    수치["현직"] = conn.execute("SELECT COUNT(*) FROM 의원 WHERE 현직여부=1").fetchone()[0]
    수치["의원_미해결"] = 미해결수
    if 현직막는것:
        로그(f"현직여부·정당·정당확인일 갱신을 건너뛴다 — {현직막는것}. 옛 값을 그대로 둔다")
    로그(f"의원 {수치['의원']}행 (현직 {수치['현직']} · 대수이력 미해결 {미해결수})")

    # ── 의원위원회 ────────────────────────────────────────────────────────────
    # ⚠️ **이 표는 통째로 갈아엎는 유일한 자리다.** 원천이 빈손인 날 그 빈손이 그대로 DB 가
    #    됐고, 게이트 어느 것도 이 표를 안 봐서 초록으로 끝났다. **지울 값을 정하기 전에
    #    쓸 값을 다 만들어 놓고 그것부터 잰다** — DELETE 뒤에 재면 기준선이 이미 없다.
    위원행 = list(c.all_pages(위원명단_API))
    있는의원 = {r[0] for r in conn.execute("SELECT 의원코드 FROM 의원")}
    쌍들 = parse_의원위원회(위원행)
    # 22대 명부 밖의 코드는 FK 가 막으므로 여기서 거른다. **거른 뒤의 수로 재야** 원천이
    # 준 수와 우리가 저장할 수 있는 수가 어긋나 가드가 헛짚는 일이 없다.
    쓸것 = [(코드, 이름) for 코드, 이름 in 쌍들 if 코드 in 있는의원]
    없는의원 = len(쌍들) - len(쓸것)
    기존 = conn.execute("SELECT COUNT(*) FROM 의원위원회").fetchone()[0]

    if 막는것 := db.교체막는것(len(쓸것), 기존):
        with db.트랜잭션(conn):
            db.상태기록(conn, "의원위원회", "전체", "건너뜀", len(쓸것), 막는것)
        수치["의원위원회"] = 기존
        수치["의원위원회_밖"] = 없는의원
        로그(f"의원위원회 교체를 건너뛴다 — {막는것}. 기존 {기존}행을 그대로 둔다")
        return 수치

    with db.트랜잭션(conn):
        conn.execute("DELETE FROM 의원위원회")
        for 코드, 위원회명 in 쓸것:
            저장 = db.upsert_위원회(conn, 위원회명)
            conn.execute(
                "INSERT OR IGNORE INTO 의원위원회 (의원코드, 위원회명) VALUES (?,?)",
                (코드, 저장),
            )
        db.상태기록(conn, "의원위원회", "전체", "완료", len(쓸것))
    수치["의원위원회"] = conn.execute("SELECT COUNT(*) FROM 의원위원회").fetchone()[0]
    수치["의원위원회_밖"] = 없는의원
    로그(f"의원위원회 {수치['의원위원회']}행 (22대 밖 의원코드 {없는의원}건 제외)")
    return 수치


def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db")
    ap.add_argument("--rate", type=float, default=8.0)
    a = ap.parse_args()

    # ⚠️ **손으로 돌리는 경로도 락을 잡는다.** 종전에는 `collect.py` 만 잡아서, 예약
    #    수집이 도는 중에 이걸 돌리면 두 프로세스가 같은 DB 에 동시에 썼다.
    # ⚠️ **연결은 락 뒤에 연다** — `connect()` 가 파일을 만들고 WAL 설정을 파일에 남기므로,
    #    앞에 두면 물러날 실행도 DB 를 건드린 뒤에 물러난다.
    경로 = db.쓸_DB(a.db)
    잠금 = db.락(경로)
    with 잠금:
        if not 잠금.잡음:
            return 3
        conn = db.connect(경로)
        db.init_schema(conn)
        try:
            with net.Client(rate=a.rate) as c:
                collect(conn, c)
        finally:
            conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
