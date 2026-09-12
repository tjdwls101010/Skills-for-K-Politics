"""이행 문이 받아들일 **진짜** 백업 세대를 만든다.

⚠️ **가짜 바이트로 만들면 안 된다.** 초안의 픽스처는 `b"x"` 한 바이트짜리 파일에
`{"검증": true}` 곁기록만 붙였는데, 그러면 **문이 사본을 실제로 열어 보는지 아닌지를
테스트가 구별하지 못한다** — 곁기록만 믿던 시절에도 그 픽스처는 초록이었다.
교차 검토(codex)가 정확히 그 지점을 짚었다.

여기서 만드는 것은 `VACUUM INTO` 로 뜬 실제 사본이고, 곁기록의 크기·지문·원본 경로가
전부 그 파일의 진짜 값이다. 문이 무엇 하나라도 다시 재기 시작하면 이 픽스처는 그대로
통과하고, 문이 거짓말을 받아들이면 아래 `망가뜨린다` 가 잡는다.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

스킬 = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "k-politics"

# ⚠️ **레포 루트를 `sys.path` 에 올리지 않는다** — 네 스킬의 `Scripts/` 이름이 겹쳐
#    먼저 import 된 쪽이 다른 스킬의 테스트를 조용히 깨뜨린다. 파일 하나만 이름 붙여 올린다.
if "backup" in sys.modules:
    백업 = sys.modules["backup"]
else:
    _명세 = importlib.util.spec_from_file_location("backup", 스킬 / "Scripts" / "backup.py")
    sys.modules["backup"] = 백업 = importlib.util.module_from_spec(_명세)
    _명세.loader.exec_module(백업)


def 세대만들기(코퍼스명: str, 원본: Path, 보관: Path, *,
               시간전: float = 1.0, 락잡음: bool = True) -> Path:
    """`원본` 을 실제로 떠서 검증된 세대 하나로 만든다. 만든 사본 경로를 준다."""
    보관.mkdir(parents=True, exist_ok=True)
    찍힘 = datetime.now(백업.KST) - timedelta(hours=시간전)
    이름 = 백업.코퍼스[코퍼스명].이름
    사본 = 보관 / f"{이름}-{찍힘:%Y-%m-%d}.db"
    사본.unlink(missing_ok=True)

    c = sqlite3.connect(f"file:{원본}?mode=ro", uri=True)
    try:
        c.execute("VACUUM INTO ?", (str(사본),))
    finally:
        c.close()

    사본.with_suffix(".json").write_text(json.dumps({
        "코퍼스": 코퍼스명,
        "날짜": f"{찍힘:%Y-%m-%d}",
        "원본": str(Path(원본).resolve()),
        "검증": True,
        "락잡음": 락잡음,
        "바이트": 사본.stat().st_size,
        # ⚠️ **실물 `뜬다()` 가 쓰는 필드는 여기서도 써야 한다.** 안 쓰면 그 필드를 읽는
        #    쪽(감사의 '직전 정상' 축)이 **기준선 없음으로 조용히 물러나고**, 테스트는
        #    "가드가 안 걸렸다" 를 "가드가 통과시켰다" 로 잘못 읽는다.
        # ⚠️ 실물은 **핵심표가 아니라 DB 안의 모든 표**를 센다(`백업._표들`). 여기서
        #    핵심표만 세면 감사의 급감 축이 실물보다 좁은 기준선으로 도는데, 그러면
        #    핵심표 밖 표에 대한 가드가 **테스트에서만 조용하다.**
        "행수": {
            t: sqlite3.connect(f"file:{사본}?immutable=1", uri=True)
                 .execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in dict.fromkeys(
                백업.코퍼스[코퍼스명].핵심표 + 백업._표들(f"file:{사본}?immutable=1")
            )
        },
        # ⚠️ **기대 지문을 `백업._해시` 로 만들지 않는다.** 문도 같은 함수로 다시 재므로
        #    그 함수가 "첫 블록만 해시"로 회귀하면 **양쪽이 똑같이 틀려 테스트가 통과한다.**
        #    오라클은 검사 대상과 다른 코드여야 한다.
        "sha256": hashlib.sha256(사본.read_bytes()).hexdigest(),
        "생성시각": 찍힘.isoformat(),
    }, ensure_ascii=False), encoding="utf-8")
    return 사본


def 망가뜨린다(사본: Path, 방식: str) -> None:
    """세대를 사고 난 상태로 만든다. 곁기록은 **건드리지 않는다** — 저장장치 사고에서
    실제로 벌어지는 일이 그것이고, 문이 곁기록의 과거 주장만 믿는지 여기서 갈린다.

    - `"잘림"`  — 파일이 1바이트로 잘렸다(크기와 지문이 둘 다 어긋난다)
    - `"내용"`  — 크기는 그대로인데 **끝쪽** 바이트가 썩었다(크기만 보는 문도, 앞부분만
                 해시하는 문도 못 잡는다 — 파일 전체를 0 으로 밀면 후자를 못 가른다)
    - `"사라짐"` — 파일이 없어졌는데 곁기록만 남았다
    """
    if 방식 == "잘림":
        사본.write_bytes(b"x")
    elif 방식 == "내용":
        원본바이트 = bytearray(사본.read_bytes())
        꼬리 = max(0, len(원본바이트) - 512)
        for i in range(꼬리, len(원본바이트)):
            원본바이트[i] ^= 0xFF
        사본.write_bytes(bytes(원본바이트))
    elif 방식 == "사라짐":
        사본.unlink()
    else:
        raise ValueError(방식)
