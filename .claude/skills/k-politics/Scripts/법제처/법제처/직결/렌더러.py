"""자르지 않는 도구. seam: (payload) → 문자열
"""

from __future__ import annotations

from .. import 원천
from ..수집기 import 법령
from 법제처 import 정규화


def 렌더_연혁(payload):
    행들 = 원천.행들(payload.get("LawSearch", {}).get("law"))
    줄들 = ["시행일자 공포일자 공포번호 제개정구분 MST"]
    for 행 in 행들:
        줄들.append(" ".join(str(v or "") for v in (
            정규화.날짜(행.get("시행일자")), 정규화.날짜(행.get("공포일자")),
            행.get("공포번호"), 행.get("제개정구분명"), 행.get("법령일련번호"))))
    return "\n".join(줄들) if 행들 else "0건"


def _번호(본, 가지=None):
    return str(int(본 or 0)) + (f"의{int(가지)}" if int(가지 or 0) else "")


def _내용줄(값):
    if isinstance(값, str):
        return 값.rstrip()
    return "\n".join(_내용줄(v) for v in (값 or []))


def 렌더_연혁본문(payload, 조=None, 부칙=False):
    본문 = payload.get("법령", {})
    단위들 = 원천.행들(본문.get("조문", {}).get("조문단위"))
    부칙들 = 원천.행들(본문.get("부칙", {}).get("부칙단위"))
    조들 = { _번호(j.get("조문번호"), j.get("조문가지번호")): (i, j)
            for i, j in enumerate(단위들) if j.get("조문여부") == "조문" }
    줄들 = []
    if 조 is not None:
        if 조 not in 조들:
            raise LookupError(f"없는 조: {조}. 있는 조: {', '.join(조들)}")
        순서, 단위 = 조들[조]
        줄들.append(법령.조문단위풀기(단위, 순서)[0]["전문"])
    elif not 부칙:
        for 번호, (_, 단위) in 조들.items():
            본, _, 가지 = 번호.partition("의")
            이름 = f"제{본}조" + (f"의{가지}" if 가지 else "")
            줄들.append(f"{이름} {단위.get('조문제목', '')} "
                        f"(조문시행일자 {정규화.날짜(단위.get('조문시행일자')) or ''})")
        줄들.append(f"부칙 {len(부칙들)}개")
    if 부칙:
        for 단위 in 부칙들:
            줄들.append(f"부칙 (공포일자 {정규화.날짜(단위.get('부칙공포일자')) or ''} · "
                        f"공포번호 {단위.get('부칙공포번호', '')})")
            줄들.append(_내용줄(단위.get("부칙내용")))
    return "\n".join(줄들)


def _렌더_별표검색(행들, 관련):
    줄들 = [f"별표번호 제목 관련{관련}명 별표종류 별표일련번호 관련{관련}일련번호 첨부"]
    for 행 in 행들:
        첨부 = "/".join(종류 for 종류, 키 in (
            ("PDF", "별표서식PDF파일링크"), ("HWP", "별표서식파일링크")) if 행.get(키))
        줄들.append(" · ".join(str(행.get(k) or "") for k in (
            "별표번호", "별표명", f"관련{관련}명", "별표종류", "별표일련번호",
            f"관련{관련}일련번호")) + f" · {첨부}")
    return "\n".join(줄들) if 행들 else "0건"


def 렌더_별표검색(payload):
    return _렌더_별표검색(원천.행들(payload.get("licBylSearch", {}).get("licbyl")), "법령")


def 렌더_행정규칙별표검색(payload):
    return _렌더_별표검색(원천.행들(payload.get("admRulBylSearch", {}).get("admrulbyl")), "행정규칙")


def _별표단위들(payload):
    본문 = payload.get("법령", payload.get("AdmRulService", {}))
    return 원천.행들(본문.get("별표", {}).get("별표단위"))


def 렌더_별표목록(payload):
    return "\n".join(
        f"{j.get('별표구분', '별표')} {_번호(j.get('별표번호'), j.get('별표가지번호'))} {j.get('별표제목', '')}"
        for j in _별표단위들(payload)) or "0건"


def _별표찾기(payload, 별표):
    본, _, 가지 = 별표.partition("의")
    번호 = (str(int(본)).zfill(4), str(int(가지 or 0)).zfill(2))
    단위들 = _별표단위들(payload)
    for 단위 in 단위들:
        if (str(단위.get("별표번호", "")).zfill(4),
                str(단위.get("별표가지번호") or "00").zfill(2)) == 번호:
            return 단위
    있는것 = ", ".join(_번호(j.get("별표번호"), j.get("별표가지번호")) for j in 단위들)
    raise LookupError(f"없는 별표: {별표}. 있는 별표: {있는것}")


def _첨부링크(단위):
    return 단위.get("별표서식PDF파일링크") or 단위.get("별표서식파일링크")


class 첨부필요(Exception):
    def __init__(self, 링크):
        self.링크 = 링크
        super().__init__(f"별표 텍스트가 비어 첨부가 필요하다: {링크}")


def 렌더_별표본문(payload, 별표):
    단위 = _별표찾기(payload, 별표)
    내용 = _내용줄(단위.get("별표내용"))
    if not 내용.strip():
        raise 첨부필요(링크=_첨부링크(단위))
    return 내용


