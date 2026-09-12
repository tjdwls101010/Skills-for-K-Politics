#!/usr/bin/env bash
# 워치독의 판정을 **이슈 하나**로 말한다.
#
# ⚠️ **매일 오는 알림은 알림이 아니다.** 국회 수집은 6일 내리 빨간불이었는데 아무도
#    안 봤다 — 같은 빨간 X 가 매일 뜨는 것이 바로 신호를 죽인 원인이다. 그래서
#    이상이 이어지는 동안 이슈는 **하나**이고 본문만 갱신한다. "언제부터 이상해졌나"는
#    이슈의 생성 시각이 답한다.
# ⚠️ **정상일 때는 조용하다.** 매일 "정상입니다" 를 남기면 이슈 목록이 쓰레기가 되고,
#    그러면 진짜 이슈가 묻힌다.
#
# 인자: <ok|bad> <본문파일>
# 환경: REPO(owner/name) · LABEL(기본 수집이상) · GH_TOKEN

# ⚠️ **식별자는 전부 ASCII 다.** bash 는 비ASCII 변수명을 거부한다 —
#    `상태=bad` 는 `command not found` 로 죽는다. 이 레포의 워치독이 정확히 그 이유로
#    도입 이후 한 번도 안 돌았고, 이 스크립트를 처음 쓸 때 나도 같은 실수를 했다.

# ⚠️ **`-e` 가 빠지면 알림이 안 갔는데 잡이 초록이다.** 이 스크립트가 하는 일은
#    「사람에게 말하기」 하나뿐이라, `gh` 가 실패하고도 0 으로 끝나면 **워치독은 성공으로
#    보이고 이슈는 없다.** 수집이 멈춘 것보다 나쁘다 — 멈춘 것은 다음 실행이 이어받지만
#    안 들린 알림에는 다음이 없다. 실패를 일부러 넘기는 자리는 `|| true` 로 명시한다.
set -euo pipefail

state="${1:?ok 또는 bad}"
body_file="${2:?본문 파일 경로}"
REPO="${REPO:?REPO 가 필요하다}"
LABEL="${LABEL:-수집이상}"
title="🔴 자동 수집이 멈췄다"

open_no=$(gh issue list --repo "$REPO" --label "$LABEL" --state open --limit 1 \
         --json number --jq '.[0].number // empty' 2>/dev/null)

if [ "$state" = "ok" ]; then
  if [ -n "$open_no" ]; then
    gh issue comment "$open_no" --repo "$REPO" --body "🟢 수집이 정상으로 돌아왔다. 닫는다."
    gh issue close "$open_no" --repo "$REPO"
    echo "이슈 #$open_no 을 닫았다."
  else
    echo "정상 — 열린 이슈가 없다. 아무것도 하지 않는다."
  fi
  exit 0
fi

if [ -n "$open_no" ]; then
  gh issue edit "$open_no" --repo "$REPO" --body-file "$body_file"
  echo "이슈 #$open_no 의 본문을 갱신했다."
else
  # 라벨이 없으면 `issue create --label` 이 실패한다. 만들어 두고 간다(있으면 그대로).
  gh label create "$LABEL" --repo "$REPO" \
     --color B60205 --description "자동 수집이 멈췄다 — 워치독이 연다" >/dev/null 2>&1 || true
  gh issue create --repo "$REPO" --title "$title" --label "$LABEL" --body-file "$body_file"
  echo "이슈를 새로 열었다."
fi
