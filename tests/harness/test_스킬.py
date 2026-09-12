"""`SKILL.md` — 이 레포가 실제로 배포하는 것.

여기서 재는 것은 산문의 품질이 아니라 **조용히 깨질 수 있는 계약**이다: 프론트매터가 유효한가,
본문의 sqlite3 명령이 안전한 모양인가, 로드가 값을 문서로 복사하지 않는가,
description 이 트리거 경계를 담고 있는가. 산문의 품질은 e2e 가 잰다.
"""

import re
from pathlib import Path

import pytest

레포 = Path(__file__).resolve().parents[2]
스킬 = 레포 / ".claude" / "skills" / "k-politics" / "SKILL.md"


@pytest.fixture(scope="module")
def 글():
    return 스킬.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def 프론트매터(글):
    m = re.match(r"^---\n(.*?)\n---\n", 글, re.S)
    assert m, "프론트매터가 `---` 로 열고 닫혀야 한다"
    return m.group(1)


def 프론트매터_고정():
    return re.match(r"^---\n(.*?)\n---\n", 스킬.read_text(encoding="utf-8"), re.S).group(1)


@pytest.fixture(scope="module")
def 본문(글):
    return re.sub(r"^---\n.*?\n---\n", "", 글, flags=re.S)


class Test프론트매터:
    def test_yaml_로_읽힌다(self, 프론트매터):
        yaml = pytest.importorskip("yaml")
        d = yaml.safe_load(프론트매터)
        assert d["name"] == "k-politics"
        assert isinstance(d["description"], str)

    def test_키가_셋뿐이다(self, 프론트매터):
        """`disable-model-invocation` 은 없다 — 대화 중 자동 트리거가 목적이다."""
        yaml = pytest.importorskip("yaml")
        assert set(yaml.safe_load(프론트매터)) == {"name", "description", "allowed-tools"}

    def test_description_이_1536자_이내다(self, 프론트매터):
        yaml = pytest.importorskip("yaml")
        assert len(yaml.safe_load(프론트매터)["description"]) <= 1536

    def test_description_이_아닌_것도_말한다(self, 프론트매터):
        """트리거 경계는 "무엇일 때"만으로는 안 선다. 이웃 스킬을 이름으로 배제해야 갈린다."""
        d = 프론트매터
        assert "News" in d and "KOSIS" in d

    def test_allowed_tools_가_본문의_레시피를_덮는다(self):
        """allow 규칙이 없으면 조회마다 권한 프롬프트가 뜬다. `${CLAUDE_SKILL_DIR}` 는
        프론트매터에서도 치환되므로 `DBs/` 아래를 `*` 둘로 덮으면 네 파일이 다 들어온다
        (실측 2026-09-12: `-box` 유무 두 규칙으로 로드와 조회가 프롬프트 없이 통과)."""
        import fnmatch

        yaml = pytest.importorskip("yaml")
        규칙 = [m.group(1) for m in re.finditer(r"Bash\((.+?)\)(?:,|$)",
                                              yaml.safe_load(프론트매터_고정())["allowed-tools"])]
        assert 규칙
        for 앞 in ('sqlite3 -box', 'sqlite3'):
            명령 = f'{앞} "file:${{CLAUDE_SKILL_DIR}}/DBs/CONGRESS.db?mode=rw" "PRAGMA query_only=1; SELECT 1"'
            assert any(fnmatch.fnmatchcase(명령, 하나 if 하나.endswith("*") else 하나 + "*")
                       for 하나 in 규칙), f"allow 규칙이 이 모양을 안 덮는다:\n  {명령}"


class Test로드비용:
    """**로드 시 조회를 주입하지 않는다.** 주입은 매 로드마다 값을 문서로 복사하는 것이고,
    그 값을 모델 대신 확인해 주는 레일이다.

    되돌리려면 이 측정을 뒤집어야 한다(2026-09-12, `run_e2e.py`): 본문 한 문장만 두고
    "22대 가상자산 법안" 을 물었더니 모델의 **두 번째 도구 호출이 `SELECT * FROM 신선도`**
    였고 적재기준시각이 답에 붙었다. 모델이 스스로 읽으므로 주입이 살 이유가 없었다.
    """

    def test_주입이_없다(self, 본문):
        주입 = re.findall(r"^!`(.+?)`$", 본문, re.M)
        assert 주입 == [], f"로드 시 조회가 남아 있다: {주입}"

    def test_신선도를_읽으라고_말한다(self, 본문):
        """본문에 날짜를 적으면 낡는다. 시점은 DB 가 갖고, 본문은 언제 그것이 중요한지만 갖는다."""
        assert "`신선도`" in 본문


class TestDB경계:
    def test_모든_sqlite3_명령이_안전한_URI_모양이다(self, 본문):
        """`-readonly` 는 -wal 없는 WAL DB 에서 죽고(errno 14), 맨 경로는 없는 자리에
        빈 DB 를 만들어 **에러 없이 0건**을 준다. 그 0건은 "없다"처럼 보인다."""
        명령들 = re.findall(r"sqlite3 [^\n]*", 본문)
        assert 명령들
        for 명령 in 명령들:
            assert "file:" in 명령, f"URI 가 아니다: {명령}"
            assert "mode=rw" in 명령 and "query_only" in 본문, f"안전하지 않다: {명령}"

    def test_네_파일을_이름으로_부른다(self, 본문):
        """파일이 곧 코퍼스다 — 어느 파일에 무엇이 있는지는 이름과 3절 한 문장이 진다."""
        for 파일 in ("NEWS.db", "CONGRESS.db", "LAW.db", "AGENCIES.db"):
            assert 파일 in 본문, 파일

    def test_사라진_층의_이름이_남아_있지_않다(self, 본문):
        """`법.db` 도 `DBs/원천/` 도 없다. `immutable=1` 은 라이브 DB 에서 최근 커밋이 빠진
        것을 읽게 하므로 이 레시피에 없다."""
        for 말 in ("법.db", "원천/", "immutable"):
            assert 말 not in 본문, 말

    def test_행_수_상한을_원리로_막는다(self, 본문):
        """래퍼가 없어 LIMIT 이 강제되지 않는다. 막는 것은 이 문장뿐이다."""
        assert "LIMIT" in 본문 and "COUNT" in 본문


class Test본문경계:
    def test_다른_스킬은_News_와_ultra_search_뿐이다(self, 글):
        """이름을 부르는 스킬이 늘면 그 이름이 바뀔 때마다 여기가 낡는다."""
        알려진 = {"News", "ultra-search", "k-politics"}
        for 이름 in re.findall(r"(?<![/\w])(congress|law|data-request|kosis|codex|Graphify|tdd)\s+스킬", 글):
            pytest.fail(f"본문이 {이름} 스킬을 부른다 — 폐기됐거나 이 스킬의 관심사가 아니다")
        assert "ultra-search" in 글 and "News" in 글
        assert 알려진

    def test_수집_파이프라인_이야기가_없다(self, 본문):
        """독자는 의원실 일을 하는 클로드다. 수집·백업·워크플로는 한 줄도 없다."""
        for 말 in ("워크플로", "GitHub Actions", "러너", "백업", "수집 주기"):
            assert 말 not in 본문, 말

    def test_의원실_정체성이_본문에_있다(self, 본문):
        """DB 조회가 아니라 본문이 진다 — 조회를 안 한 턴에는 없는 것과 같기 때문이다."""
        assert "김형연" in 본문 and "정무위원회" in 본문 and "KBZ59750" in 본문

    def test_취임일_이전은_전임자라고_말한다(self, 본문):
        assert "취임일 이전" in 본문 or "이전의 발의" in 본문

    def test_본문이_120줄_이내다(self, 본문):
        """길이 자체가 목적은 아니고, 넘으면 컴팩션에서 먼저 잘리는 부분이 생긴다."""
        줄 = [l for l in 본문.splitlines() if l.strip()]
        assert len(줄) <= 120, f"{len(줄)}줄"

    def test_문단에_하드랩이_없다(self, 본문):
        """읽는 쪽이 받는 텍스트에서 의미가 나뉘는 곳에만 줄바꿈을 둔다. 80자에서 끊으면
        `grep`·줄 범위·`Edit` 정확 일치가 무엇에 대한 말인지 잃은 조각을 집는다."""
        코드밖 = re.sub(r"```.*?```", "", 본문, flags=re.S)
        줄 = 코드밖.splitlines()
        for i, l in enumerate(줄[:-1]):
            다음 = 줄[i + 1].strip()
            if not l.strip() or l.startswith(("#", "|", "-", "!")) or not 다음:
                continue
            if 다음.startswith(("#", "|", "-", "!", "```")):
                continue
            pytest.fail(f"{i + 1}행이 다음 줄로 이어진다(하드랩):\n  {l}\n  {다음}")


class Test본문이스키마를베끼지않는다:
    """**DB 가 말할 수 있는 것을 본문에 옮겨 적지 않는다.** 두 곳에 적으면 한쪽만 고치게 되고,
    본문은 매 로드마다 컨텍스트를 먹는데 스키마는 필요할 때만 읽힌다.

    한 번 어겼다 — "감사통과가 0이면 …" 문단을 본문에 넣었는데, 그 지식의 자리는 그 컬럼의
    주석이다(수집기 SCHEMA 의 `신선도` 뷰). 읽는 사람은 이미 거기 있다.
    """

    def test_컬럼의_의미를_본문이_설명하지_않는다(self, 본문):
        for 컬럼 in ("감사통과", "감사상세", "적재기준시각", "매칭근거", "실패종류"):
            assert f"`{컬럼}`" not in 본문 and f"**`{컬럼}`" not in 본문, (
                f"{컬럼} 의 뜻은 `.schema` 가 말한다 — 본문에 옮겨 적으면 두 곳이 갈린다")

    def test_표_이름_목록을_본문에_적지_않는다(self, 본문):
        """목록은 `sqlite_master` 가 갖는다. 적어 두면 수집기가 표를 늘린 날 낡는다."""
        코드밖 = 본문.split("```")[0] + "".join(본문.split("```")[2::2])
        적힌것 = [t for t in ("의안심사", "표결집계", "회의의안", "의율조문", "인용판례",
                            "법률안제안주체", "조문판단수", "조문판단", "위임대상조문")
                 if t in 코드밖]
        assert not 적힌것, f"본문이 표 이름을 나열한다: {적힌것}"
