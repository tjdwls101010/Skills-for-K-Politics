"""law: `⚠` 줄이 `sqlite_master` 에 도달하는가 (congress S1 대응)."""


def test_경고_주석이_전부_살아남는다(conn):
    """⚠️ SQLite 는 `CREATE TABLE`·`CREATE VIEW` **밖**의 주석을 버린다.

    조회하는 쪽은 `.schema` 만 보므로, 버려진 경고는 **없는 것과 같다.**
    2026-08-22 재작성 전까지 law 는 괄호 밖 주석이 14줄이었고 그중 ⚠ 가 3줄이었다 —
    거기 **4-way UNION 성능 절벽**이 들어 있었다. `.schema` 에도 SKILL.md 에도 없던 것이다.

    congress 는 맨 위 `PRAGMA` 줄 하나가 예외지만 law 의 SCHEMA 에는 PRAGMA 가 없어
    **0건이 정답이다.**
    """
    from 법제처 import 스키마

    stored = "\n".join(
        r[0] for r in conn.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")
    )
    lost = [l.strip() for l in 스키마.SCHEMA.splitlines() if "⚠" in l and l.strip() not in stored]
    assert lost == [], f"`.schema` 에 도달하지 못하는 경고: {lost}"
