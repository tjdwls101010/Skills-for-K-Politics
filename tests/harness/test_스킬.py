"""`SKILL.md` — 이 레포가 실제로 배포하는 것.

여기서 재는 것은 산문의 품질이 아니라 **조용히 깨질 수 있는 계약**이다: 프론트매터가 유효한가,
본문의 sqlite3 명령이 안전한 모양인가, 주입 명령이 스킬을 통째로 못 쓰게 만들 수 있는가,
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

    def test_allowed_tools_가_주입_명령을_덮는다(self, 프론트매터, 본문):
        """**주입 명령은 권한 프롬프트를 안 띄운다.** allow 규칙이 없으면 그냥 실패하고,
        0 아닌 종료 코드는 에러 한 줄이 아니라 **스킬 호출 전체를 중단**시킨다 — 클로드가
        본문을 통째로 못 받는다(2026-09-12 문서 확인). 계획의 D13("allowed-tools 없음")이
        이 실측으로 뒤집힌 자리다."""
        yaml = pytest.importorskip("yaml")
        허용 = yaml.safe_load(프론트매터)["allowed-tools"]
        for 주입 in re.findall(r"^!`(.+?)`$", 본문, re.M):
            앞 = 주입.split('"')[0].strip()      # 'sqlite3 -box' 또는 'sqlite3'
            db = re.search(r"DBs/([^?]+)\?([^\"]*)", 주입)
            assert db, 주입
            패턴 = f'{앞} "file:${{CLAUDE_SKILL_DIR}}/DBs/{db.group(1)}?{db.group(2)}"'
            assert 패턴 in 허용, f"allow 규칙이 이 주입을 안 덮는다:\n  {패턴}"


class Test주입명령:
    def test_모든_주입에_폴백이_있다(self, 본문):
        """sqlite3 는 DB 가 없거나 잠기면 비영으로 죽고, 그러면 스킬 자체가 안 열린다.
        `|| echo` 가 그 실패를 **본문 안의 한 줄**로 낮춘다 — 코퍼스가 없다는 것을 클로드가
        알면서도 나머지 규율은 받는다."""
        주입 = re.findall(r"^!`(.+?)`$", 본문, re.M)
        assert 주입, "로드 시 주입이 하나도 없다"
        for 명령 in 주입:
            assert "2>&1" in 명령, f"stderr 를 안 삼킨다: {명령}"
            assert "|| echo" in 명령, f"폴백이 없다 — 실패하면 스킬이 통째로 안 열린다: {명령}"

    def test_폴백_문구가_무엇을_하라를_말한다(self, 본문):
        for 명령 in re.findall(r"^!`(.+?)`$", 본문, re.M):
            폴백 = 명령.split("|| echo", 1)[1]
            assert len(폴백) > 30, f"폴백이 상태만 말하고 대안을 안 준다: {폴백}"

    def test_신선도를_주입한다(self, 본문):
        """본문에 날짜를 적으면 낡는다. 매일 바뀌는 것만 DB 에서 읽어 온다."""
        assert "FROM 신선도" in 본문


class TestDB경계:
    def test_모든_sqlite3_명령이_안전한_URI_모양이다(self, 본문):
        """`-readonly` 는 -wal 없는 WAL DB 에서 죽고(errno 14), 맨 경로는 없는 자리에
        빈 DB 를 만들어 **에러 없이 0건**을 준다. 그 0건은 "없다"처럼 보인다."""
        명령들 = re.findall(r"sqlite3 [^\n]*", 본문)
        assert 명령들
        for 명령 in 명령들:
            if "<SQL>" not in 명령 and "!`" not in f"!`{명령}":
                pass
            assert "file:" in 명령, f"URI 가 아니다: {명령}"
            assert ("mode=ro&immutable=1" in 명령
                    or ("mode=rw" in 명령 and "query_only" in 본문)), f"안전하지 않다: {명령}"

    def test_원천을_열지_않는다(self, 본문):
        """`DBs/원천/` 은 수집기가 쓰는 라이브 파일이다."""
        for 명령 in re.findall(r"sqlite3 [^\n]*", 본문):
            assert "원천/" not in 명령
        assert "`DBs/원천/`은" in 본문, "열지 않는다는 것을 본문이 말해야 한다"

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
        """목록은 로드 시 주입한다. 적어 두면 수집기가 표를 늘린 날 낡는다."""
        코드밖 = 본문.split("```")[0] + "".join(본문.split("```")[2::2])
        적힌것 = [t for t in ("의안심사", "표결집계", "회의의안", "조문요소", "의율조문",
                            "행정규칙조문", "인용판례", "법률안제안주체", "조문판단수")
                 if t in 코드밖]
        assert not 적힌것, f"본문이 표 이름을 나열한다: {적힌것}"
