"""`Scripts/direct.py` — 자르지 않는 도구. seam: (payload) → 문자열 · (첨부 bytes) → 텍스트 · CLI.

e2e(2026-09-10) 실측: `연혁본문` 225,871B 를 `[:200000]` 이 JSON 중간에서 끊어 파싱 불가(M4, Opus 복구
10턴) · 별표는 링크만이라 Opus V7 이 37턴 동안 curl→PDF→HWP 를 손으로 팠다(M5). 원천 실측(2026-09-11):
본문 API(`target=law`·`admrul`)가 그 법의 별표 목록과 **별표 본문 텍스트(표 포함)** 를 `별표단위.별표내용`
에 인라인으로 준다 — 첨부(PDF/HWP)는 텍스트가 빈 서식류의 폴백이다.

픽스처는 `tests/law/fixtures/직결/` — 실호출 원본에서 조 몇 개·부칙 두 개만 남긴 것(구조는 그대로).
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "k-politics" / "Scripts" / "law"
FX = Path(__file__).resolve().parent / "fixtures" / "직결"

렌더러 = pytest.importorskip("law.live.render")
from law.collectors import statute as 법령
첨부 = pytest.importorskip("law.live.attachment")
커맨드표 = pytest.importorskip("law.live.commands")


def _픽스처(이름):
    return json.loads((FX / 이름).read_text(encoding="utf-8"))


class Test연혁:
    def test_시행일마다_한_행이고_링크는_없다(self):
        글 = 렌더러.렌더_연혁(_픽스처("eflaw_연혁_전금법.json"))
        줄들 = 글.strip().splitlines()
        assert 줄들[0].split() [:4] == ["시행일자", "공포일자", "공포번호", "제개정구분"] or "시행일자" in 줄들[0]
        assert any("2024-09-15" in 줄 and "254921" in 줄 for 줄 in 줄들)
        assert "lawService" not in 글 and "OC=" not in 글

    def test_빈_결과와_단수_응답(self):
        빈 = {"LawSearch": {"totalCnt": "0", "law": []}}
        assert "0건" in 렌더러.렌더_연혁(빈)
        단수 = {"LawSearch": {"totalCnt": "1", "law": {"법령일련번호": "1", "시행일자": "20200101",
                 "공포일자": "20191231", "공포번호": "1", "제개정구분명": "제정", "법령명한글": "가상법"}}}
        assert "2020-01-01" in 렌더러.렌더_연혁(단수)


class Test연혁본문:
    payload = None

    @classmethod
    def setup_class(cls):
        cls.payload = _픽스처("eflaw_연혁본문_개보법_270351.json")

    def test_기본은_조_목차와_부칙_개수다(self):
        글 = 렌더러.렌더_연혁본문(self.payload)
        assert "제28조의2" in 글 and "가명정보의 처리 등" in 글
        assert "제3절" not in 글                       # 편·장·절 제목 행은 조가 아니다
        assert "부칙 2개" in 글
        assert "2025-10-02" in 글                       # 조문시행일자

    def test_조를_주면_전문이_DB_현행조문과_같은_조립이다(self, conn):
        """`법령.전문조립` 과 같은 코드로 조립한다 — 수집기와 렌더러가 다른 전문을 내면
        「DB 가 틀렸나 API 가 틀렸나」를 아무도 못 가른다."""
        단위 = next(j for j in self.payload["법령"]["조문"]["조문단위"]
                  if j.get("조문번호") == "28" and j.get("조문가지번호") == "2" and j.get("조문여부") == "조문")
        전문 = 법령.전문조립(단위)
        글 = 렌더러.렌더_연혁본문(self.payload, 조="28의2")
        assert 전문 in 글
        assert 글.count("제28조의2(가명정보의 처리 등)") == 1

    def test_없는_조는_있는_조를_알려주며_거부한다(self):
        with pytest.raises(LookupError) as e:
            렌더러.렌더_연혁본문(self.payload, 조="999")
        assert "28의2" in str(e.value)

    def test_부칙은_전문이다(self):
        글 = 렌더러.렌더_연혁본문(self.payload, 부칙=True)
        assert "제1조(시행일)" in 글 and "20897" in 글
        assert "['" not in 글                             # 파이썬 리스트 repr 이 새면 안 된다


class Test별표검색:
    def test_법령_별표_검색_표(self):
        글 = 렌더러.렌더_별표검색(_픽스처("licbyl_과징금.json"))
        assert "물납신청서" in 글 and "부동산 실권리자명의 등기에 관한 법률 시행규칙" in 글
        assert "193041" in 글 and "6865195" in 글         # 관련법령일련번호(MST) · 별표일련번호
        assert "PDF" in 글 or "pdf" in 글                  # 첨부 종류
        assert "flDownload" not in 글

    def test_행정규칙_별표_검색_표(self):
        글 = 렌더러.렌더_행정규칙별표검색(_픽스처("admbyl_평가범위.json"))
        assert "중앙전파관리소 정보보안 관리지침" in 글 and "2100000274026" in 글

    def test_빈_결과(self):
        assert "0건" in 렌더러.렌더_별표검색({"licBylSearch": {"totalCnt": "0"}})


class Test별표목록과본문:
    payload = None

    @classmethod
    def setup_class(cls):
        cls.payload = _픽스처("law_본문_개보법시행령_283503.json")

    def test_목록은_그_법의_별표_전부다(self):
        글 = 렌더러.렌더_별표목록(self.payload)
        줄들 = [줄 for 줄 in 글.splitlines() if 줄.strip()]
        assert sum("별표" in 줄 for 줄 in 줄들) >= 6
        assert "1의5" in 글 and "과징금의 산정기준과 산정절차" in 글
        assert "별표 2" in 글 or "2 " in 글

    def test_본문은_인라인_텍스트이고_표의_행_열이_줄로_보존된다(self):
        글 = 렌더러.렌더_별표본문(self.payload, "1의5")
        assert "과징금의 산정기준과 산정절차" in 글
        assert "매우 중대한 위반행위" in 글 and "2.1% 이상 2.7% 이하" in 글
        줄 = next(l for l in 글.splitlines() if "매우 중대한 위반행위" in l)
        assert "2.1%" in 줄                                # 같은 행의 열이 같은 줄에

    def test_별표_2_와_1의5_는_다른_별표다(self):
        assert "과태료의 부과기준" in 렌더러.렌더_별표본문(self.payload, "2")
        assert "과태료의 부과기준" not in 렌더러.렌더_별표본문(self.payload, "1의5")

    def test_없는_별표는_있는_것을_알려주며_거부한다(self):
        with pytest.raises(LookupError) as e:
            렌더러.렌더_별표본문(self.payload, "9")
        assert "1의5" in str(e.value)

    def test_텍스트가_빈_별표는_첨부를_가리킨다(self):
        p = json.loads(json.dumps(self.payload))
        단위 = p["법령"]["별표"]["별표단위"][0]
        단위["별표내용"] = []
        with pytest.raises(렌더러.첨부필요) as e:
            렌더러.렌더_별표본문(p, "1")
        assert "flSeq=" in str(e.value.링크)


class Test첨부:
    def test_PDF_에서_텍스트를_뽑는다(self):
        글 = 첨부.첨부텍스트((FX / "별표_서식_29376666.pdf").read_bytes())
        assert "물납" in 글

    def test_HWP_에서_텍스트를_뽑는다(self):
        글 = 첨부.첨부텍스트((FX / "별표_서식_29376650.hwp").read_bytes())
        assert "물납" in 글

    def test_모르는_형식은_거부한다(self):
        with pytest.raises(ValueError):
            첨부.첨부텍스트(b"<html>not a document</html>")


class 가짜클라이언트:
    """`원천.Client` 자리. 어떤 요청이든 정해 둔 payload 를 준다."""
    마지막 = None

    def __init__(self, payload, **_):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass

    def _json(self, url, q):
        가짜클라이언트.마지막 = (url, q)
        return self.payload


class TestCLI:
    def _돌린다(self, monkeypatch, capsys, payload, *인자):
        monkeypatch.setattr(커맨드표.원천, "Client", lambda **kw: 가짜클라이언트(payload))
        코드 = 커맨드표._main(list(인자))
        나옴 = capsys.readouterr()
        return 코드, 나옴.out, 나옴.err

    def test_원본은_자르지_않는다(self, monkeypatch, capsys):
        """M4. 225KB 응답이 200,000자에서 끊겨 JSON 이 깨졌다."""
        p = _픽스처("eflaw_연혁본문_개보법_270351.json")
        p["법령"]["개정문"] = {"개정문내용": ["x" * 300_000]}
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "연혁본문", "--법령일련번호", "270351", "--시행일", "20251002", "--원본")
        assert 코드 == 0
        assert len(json.loads(out)["법령"]["개정문"]["개정문내용"][0]) == 300_000

    def test_연혁본문은_기본이_목차이고_조를_고른다(self, monkeypatch, capsys):
        p = _픽스처("eflaw_연혁본문_개보법_270351.json")
        _, out, _ = self._돌린다(monkeypatch, capsys, p, "연혁본문", "--법령일련번호", "270351", "--시행일", "2025-10-02")
        assert "제28조의2" in out and "가명정보를 처리할 수 있다" not in out
        _, out, _ = self._돌린다(monkeypatch, capsys, p, "연혁본문", "--법령일련번호", "270351", "--시행일", "2025-10-02", "--조", "28의2")
        assert "가명정보를 처리할 수 있다" in out
        assert 가짜클라이언트.마지막[1]["efYd"] == "20251002"

    def test_별표목록은_본문_API_로_간다(self, monkeypatch, capsys):
        p = _픽스처("law_본문_개보법시행령_283503.json")
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "별표목록", "--법령일련번호", "283503")
        assert 코드 == 0 and "1의5" in out
        assert 가짜클라이언트.마지막[1]["target"] == "law" and 가짜클라이언트.마지막[1]["MST"] == "283503"

    def test_별표본문은_인라인_텍스트를_낸다(self, monkeypatch, capsys):
        p = _픽스처("law_본문_개보법시행령_283503.json")
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "별표본문", "--법령일련번호", "283503", "--별표", "1의5")
        assert 코드 == 0 and "2.1% 이상 2.7% 이하" in out

    def test_행정규칙_일련번호로도_별표목록을_부른다(self, monkeypatch, capsys):
        p = {"AdmRulService": {"별표": {"별표단위": [{"별표번호": "0001", "별표가지번호": "00", "별표구분": "별표",
             "별표제목": "정보기술부문 및 정보보호 인력 산정기준", "별표내용": [["■ 전자금융감독규정 [별표 1]", "  인력 산정기준"]]}]}}}
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "별표목록", "--행정규칙일련번호", "2100000282622")
        assert 코드 == 0 and "인력 산정기준" in out
        assert 가짜클라이언트.마지막[1]["target"] == "admrul" and 가짜클라이언트.마지막[1]["ID"] == "2100000282622"

    def test_저장은_파일에_원본을_쓴다(self, monkeypatch, capsys, tmp_path):
        p = _픽스처("eflaw_연혁_전금법.json")
        경로 = tmp_path / "연혁.json"
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "연혁", "--법령", "전자금융거래법", "--저장", str(경로))
        assert 코드 == 0 and json.loads(경로.read_text(encoding="utf-8")) == p
        assert "2024-09-15" in out                     # 렌더링은 그대로 stdout

    def test_렌더러가_없는_커맨드는_원본_JSON_한_줄이다(self, monkeypatch, capsys):
        p = {"lsStmd": {"x": "y" * 1000}}
        코드, out, _ = self._돌린다(monkeypatch, capsys, p, "체계도", "--법령", "형법")
        assert 코드 == 0 and json.loads(out) == p and out.count("\n") <= 1

    def test_help_가_새_커맨드와_인자를_진다(self):
        r = subprocess.run([sys.executable, str(SCRIPTS / "direct.py"), "--help"], capture_output=True, text=True)
        assert r.returncode == 0
        for 말 in ("별표목록", "별표본문", "--조", "--부칙", "--별표", "--원본", "--저장", "--행정규칙일련번호"):
            assert 말 in r.stdout, 말
        assert "200000" not in r.stdout


def test_의존성에_pypdf_와_olefile_이_있다():
    머리 = (SCRIPTS / "direct.py").read_text(encoding="utf-8").split('"""', 1)[0]
    assert "pypdf" in 머리 and "olefile" in 머리


class Test행정규칙:
    """⚠️ **행정규칙은 이 DB 에 없다.** 본문 326MB 의 98%가 정무위 밖 부처라 담지 않기로
    했고, 감독규정처럼 실제로 필요한 것은 여기서 실시간으로 본다 — 이 두 커맨드가
    "DB 에 있는 자료의 커맨드는 여기 없다"는 원칙이 **가리키는 바깥**이다.
    """

    def test_검색이_고를_수_있는_줄로_온다(self):
        """일련번호가 줄에 있어야 다음 한 수(`행정규칙본문`)로 이어진다 — 없으면 사람이
        원본 JSON 을 뒤져야 한다."""
        글 = 렌더러.렌더_행정규칙검색(_픽스처("admrul_감독규정.json"))
        줄들 = 글.strip().splitlines()
        assert 줄들[0].split()[:3] == ["행정규칙명", "종류", "소관부처"]
        assert any("감독규정" in 줄 and "21000002" in 줄 for 줄 in 줄들[1:])

    def test_빈_결과(self):
        assert "0건" in 렌더러.렌더_행정규칙검색({"AdmRulSearch": {"admrul": []}})

    def test_본문_봉투를_벗긴다(self):
        """⚠️ **본문은 `AdmRulService` 봉투에 싸여 온다.** 안 벗기면 모든 필드가 비어
        제목도 본문도 없는 머리 두 줄만 나온다 — 에러 없이, 0건처럼 보이면서."""
        글 = 렌더러.렌더_행정규칙본문(_픽스처("admrul_본문_가상자산업감독규정.json"))
        assert 글.startswith("가상자산업감독규정 (고시)")
        assert "금융위원회" in 글 and "2024-07-19" in 글
        assert "제1조(목적)" in 글

    def test_조문내용이_문자열_하나로_와도_한_덩어리로_낸다(self):
        """⚠️ `조문형식여부='N'`(실측 10%)이면 배열이 아니라 **문자열 하나**로 온다 —
        배열로 알고 순회하면 본문이 세로로 한 글자씩 찍힌다. 에러 없이."""
        글 = 렌더러.렌더_행정규칙본문({"AdmRulService": {
            "행정규칙기본정보": {"행정규칙명": "국호에 관한 건", "행정규칙종류": "고시"},
            "조문내용": "1. 우리나라의 정식 국호는 「대한민국」이나 …"}})
        assert "1. 우리나라의 정식 국호는 「대한민국」이나 …" in 글
        assert "1\n." not in 글

    def test_커맨드표에_둘_다_있고_DB_자료와_안_겹친다(self):
        """⚠️ DB 에 있는 자료의 커맨드가 여기 생기면 완결적인 DB 를 두고 API 로 물어
        **상위 N 건만 보고 답하게** 된다. 경계는 `source.봉투` 가 진다."""
        from law import source as 원천
        assert {"행정규칙", "행정규칙본문"} <= set(커맨드표.커맨드)
        assert not set(커맨드표.커맨드) & set(원천.봉투)

    def test_행정규칙본문은_일련번호를_요구한다(self):
        """검색어로 부르면 원천이 0건을 주고, 0건은 "그런 규칙이 없다"로 읽힌다."""
        with pytest.raises(SystemExit):
            커맨드표._main(["행정규칙본문", "--검색", "감독규정"])

    def test_부처는_행정규칙_검색에만_쓴다(self):
        with pytest.raises(SystemExit):
            커맨드표._main(["연혁", "--부처", "금융위원회"])
