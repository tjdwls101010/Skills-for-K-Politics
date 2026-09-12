"""`.github/scripts/요약.py` — 국회·법령 두 워크플로가 함께 쓰는 요약 렌더러.

⚠️ **여기서 잠그는 계약은 하나로 요약된다: 요약은 절대 실패하지 않는다.** 요약이
   0 아닌 것으로 끝나면 진짜 판정 옆에 **두 번째 빨간불**이 생기고, 그러면 사람이
   어느 쪽이 진짜인지 매번 다시 판단하게 된다. 최종 판정은 `판정 전파` 가 `always()`
   로 따로 낸다.

⚠️ **`종류` 가 문구를 소유한다.** 종료코드로 문구를 정하면 법령의 인증 실패(1)가
   게이트 위반으로 읽히고, 락에 막힌 3 은 실패가 아닌데 코드만 보면 그것을 모른다.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

렌더러 = Path(__file__).resolve().parents[2] / ".github" / "scripts" / "요약.py"


def 돌린다(tmp_path, 곁기록=None, **인자) -> str:
    명령 = [sys.executable, str(렌더러), "--제목", "법령 수집"]
    if 곁기록 is not None:
        경로 = tmp_path / "result.json"
        경로.write_text(곁기록 if isinstance(곁기록, str)
                      else json.dumps(곁기록, ensure_ascii=False))
        명령 += ["--결과", str(경로)]
    for k, v in 인자.items():
        명령 += [f"--{k}", str(v)]
    p = subprocess.run(명령, capture_output=True, text=True, cwd=tmp_path)
    assert p.returncode == 0, f"요약이 {p.returncode} 로 끝났다\n{p.stderr}"
    return p.stdout


def _판(**덮을것) -> dict:
    판 = {"코퍼스": "law", "종류": "정상", "판정": 0, "감사실행": True, "소요초": 60.0,
         "단계": [{"이름": "법령", "수치": {"목록": 6433}}], "유량": {"요청": 12345},
         "게이트": [{"번호": "A1", "이름": "적재 건수", "값": 0, "ok": True, "해석": None}],
         "보고": [{"번호": "R22", "이름": "부분 수신", "값": "없음"}]}
    판.update(덮을것)
    return 판


def test_제목과_시각이_맨_위에_온다(tmp_path) -> None:
    글 = 돌린다(tmp_path, _판())
    assert 글.splitlines()[0].startswith("## 법령 수집 · ")
    assert "KST" in 글.splitlines()[0]


def test_초록은_한_줄로_끝나고_게이트_표를_안_펼친다(tmp_path) -> None:
    글 = 돌린다(tmp_path, _판())
    assert "🟢 정상" in 글
    assert "### 🔴 위반한 게이트" not in 글
    assert "<details><summary>통과한 게이트 1건</summary>" in 글


def test_빨간_게이트가_맨_위_표로_오고_해석이_따라_붙는다(tmp_path) -> None:
    """⚠️ **이게 이 렌더러의 존재 이유다.** 종전에는 스물몇 줄짜리 고정폭 표를 코드펜스에
    그대로 부어서, 🔴 한둘을 눈으로 찾아야 했다."""
    글 = 돌린다(tmp_path, _판(종류="게이트", 판정=1, 게이트=[
        {"번호": "A11", "이름": "설명 안 되는 중복", "값": 2, "ok": False,
         "해석": "정리가 멈췄다"},
        {"번호": "A1", "이름": "적재 건수", "값": 0, "ok": True, "해석": None}]))
    assert "🔴 **게이트 1건 위반 — 사람이 봐야 한다**" in 글
    위반표 = 글.index("### 🔴 위반한 게이트")
    접힌것 = 글.index("<details><summary>통과한 게이트")
    assert 위반표 < 접힌것, "초록이 빨강보다 위에 있으면 읽는 순서가 뒤집힌다"
    assert "| A11 | 설명 안 되는 중복 | 2 |" in 글
    assert "> **A11** — 정리가 멈췄다" in 글


@pytest.mark.parametrize(("종류", "조각"), [
    ("락", "⚪"), ("중단", "다음 실행이 이어받는다"), ("인증", "인증 실패"),
    ("예외", "뜻밖의 예외"),
])
def test_종류마다_문구가_갈린다(tmp_path, 종류, 조각) -> None:
    글 = 돌린다(tmp_path, _판(종류=종류, 감사실행=False, 게이트=[], 보고=[]))
    assert 조각 in 글


def test_감사가_안_돌았으면_게이트_표를_그리지_않고_로그를_붙인다(tmp_path) -> None:
    """⚠️ **빈 게이트 배열을 "전부 통과"로 읽으면 안 된다.** 락에 막히거나 중간에 죽은
    판은 감사가 아예 안 돈 것이고, 그걸 초록으로 그리면 요약이 거짓말을 한다."""
    (tmp_path / "collect.txt").write_text("마지막 줄이다\n")
    글 = 돌린다(tmp_path, _판(종류="락", 감사실행=False, 게이트=[], 보고=[]))
    assert "감사는 돌기 전에 끝났다" in 글
    assert "마지막 줄이다" in 글
    assert "통과한 게이트" not in 글


def test_부분수신은_게이트가_없으므로_윗머리에_따로_적는다(tmp_path) -> None:
    """⚠️ A25 는 '건너뜀' 만 센다 — 흔한 날에 빨개지면 늑대를 부르므로 일부러 그렇다.
    그래서 정리가 며칠째 멈춰 있어도 판정은 초록이고, **이 줄이 유일한 눈이다.**"""
    글 = 돌린다(tmp_path, _판(보고=[{"번호": "R22", "이름": "부분 수신", "값": "판례 3일째"}]))
    assert "정리가 부분 수신으로 멈춰 있다 — 판례 3일째" in 글
    assert 글.index("부분 수신으로 멈춰") < 글.index("### 이번 실행")


def test_곁기록이_깨졌어도_0_으로_끝나고_로그를_보여준다(tmp_path) -> None:
    (tmp_path / "collect.txt").write_text("죽기 직전 줄\n")
    글 = 돌린다(tmp_path, "{이건 JSON 이 아니다")
    assert "수집 결과를 읽지 못했다" in 글
    assert "죽기 직전 줄" in 글


def test_곁기록이_아예_없어도_0_으로_끝난다(tmp_path) -> None:
    """SIGKILL·러너 유실·잡 타임아웃에서는 `finally` 가 안 돈다 — 그날이 이 갈래다."""
    글 = 돌린다(tmp_path)
    assert "수집 결과를 읽지 못했다" in 글


def test_preflight_가_빨가면_원문_꼬리를_그대로_낸다(tmp_path) -> None:
    """⚠️ **그날은 `result.json` 이 애초에 없다** — 수집 스텝이 안 돌았다.
    구조화할 것이 없는 갈래에서는 날것이 맞다."""
    (tmp_path / "preflight.txt").write_text("🔴 DB 가 없다: /어딘가/LAW.db\n")
    글 = 돌린다(tmp_path, preflight="failure")
    assert "DB 경로 점검 실패" in 글
    assert "🔴 DB 가 없다: /어딘가/LAW.db" in 글


def test_verify_가_빨가면_수집을_건너뛴_사실을_말한다(tmp_path) -> None:
    (tmp_path / "verify.txt").write_text("V3 원천 필드가 사라졌다\n")
    글 = 돌린다(tmp_path, verify="1")
    assert "검증 실패" in 글 and "V3 원천 필드가 사라졌다" in 글


def test_유량_라벨은_실제로_잰_것만_말한다(tmp_path) -> None:
    """⚠️ 두 수집기가 세는 것이 다르다 — 법령은 요청과 백오프를, 국회는 백오프만 센다.
    고정 라벨이면 「요청 · 백오프 | 백오프 0」처럼 **있지도 않은 값을 약속한다.**"""
    글 = 돌린다(tmp_path, _판(유량={"백오프": 0}))
    assert "| 유량 | 백오프 0 |" in 글
    assert "요청" not in 글.split("### 이번 실행")[1].split("</details>")[0]


def test_국회의_표_모양도_같은_렌더러가_그린다(tmp_path) -> None:
    """두 수집기가 서로 다른 것을 센다 — 법령은 자료종류별 수치, 국회는 테이블 증분.
    한 모양으로 억지로 합치지 않고, 있는 쪽만 그린다."""
    글 = 돌린다(tmp_path, _판(코퍼스="congress", 단계=[],
                          표=[{"이름": "의안", "이전": 20916, "이후": 20918, "증분": 2}]))
    assert "| 의안 | 20,916 → 20,918 (+2) |" in 글


@pytest.mark.parametrize("망가진판", [
    {"종류": "정상", "감사실행": True, "게이트": [None]},
    {"종류": "정상", "감사실행": True, "게이트": ["문자열"], "보고": [None]},
    {"종류": "정상", "감사실행": True, "단계": [None], "표": [None]},
    {"종류": "정상", "감사실행": True, "게이트": "배열이 아니다"},
    {"종류": None, "감사실행": True},
], ids=["게이트_null", "보고_null", "단계와_표_null", "게이트가_배열이_아님", "종류_null"])
def test_곁기록_모양이_망가져도_0_으로_끝난다(tmp_path, 망가진판) -> None:
    """⚠️ **JSON 이 파싱된다고 모양이 맞는 것은 아니다.** 곁기록을 쓰는 쪽이 중간에
    죽으면 배열 원소가 `null` 인 반쪽짜리 파일이 남을 수 있고, 그때 렌더러가 예외로
    새어 나가면 **진짜 판정 옆에 두 번째 빨간불**이 생긴다 — 이 검사가 없을 때
    실제로 `AttributeError` 로 1 이 나갔다.
    """
    (tmp_path / "collect.txt").write_text("로그는 남아 있다\n")
    글 = 돌린다(tmp_path, 망가진판)
    assert 글.splitlines()[0].startswith("## 법령 수집 · ")


def test_표를_깨뜨리는_값이_와도_표가_안_깨진다(tmp_path) -> None:
    """보고값에는 사람이 쓴 문장이 들어온다 — `|` 하나가 표 전체를 어긋나게 한다."""
    글 = 돌린다(tmp_path, _판(보고=[{"번호": "R1", "이름": "a|b", "값": "c|d"}]))
    assert "| R1 | a\\|b | c\\|d |" in 글
