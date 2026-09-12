#!/usr/bin/env bash
# 수집이 멈췄는지 **맥 밖에서** 본다.
#
# 보는 것은 "마지막으로 언제 잘 됐나" 하나다. 수집 로그가 아니라 실행 이력을 본다 —
# 워크플로가 **아예 안 뜬 경우**(러너가 죽었거나 GitHub 이 예약을 흘린 경우)를 잡는 것이
# 목적이라, 로그를 보면 그 경우가 애초에 없다. 실측으로 2026-08-16·17 이틀은 국회·법령
# 예약 실행이 실행 기록조차 없었다.
#
# ⚠️ **식별자는 전부 ASCII 다.** bash 는 비ASCII 변수명을 거부한다 —
#    `문제=0` 은 `command not found` 로 exit 127 이 되고, 그러면 감시자가 첫 줄에서
#    죽은 채 매일 빨간불만 낸다. 실제로 도입(8/13) 이후 감시를 한 번도 못 했다.
#    같은 함정이 `.claude/hooks/신선도.sh` 주석에 이미 적혀 있었는데 여기서 반복됐다.
#
# 인자: `<워크플로파일>:<모드>` 여럿. 모드는 success | run.
#   success — 마지막 **성공**이 오래됐으면 문제. 수집이 그렇다(실패한 수집은 문제다).
#   run     — 마지막 **실행**이 오래됐으면 문제. 감시자 자신이 그렇다.
#             자기 자신에 success 를 쓰면, 수집이 멈춰서 자기가 빨개진 것 때문에
#             다음 날 자기도 늙은 것으로 세어져 **문제 하나가 둘로 불어난다.**
#
# 환경: REPO(owner/name) · STALE_HOURS · GH_TOKEN
# 출력: 이슈 본문에 그대로 들어갈 마크다운 줄들
# 종료코드: 0 정상 · 1 사람이 봐야 한다

set -uo pipefail

REPO="${REPO:?REPO 가 필요하다}"
STALE_HOURS="${STALE_HOURS:-30}"

epoch() {
  # ISO8601(Z) → epoch. `date -d`(GNU)와 `date -j -f`(BSD)가 갈리므로 python3 로 끝낸다 —
  # 이 스크립트는 ubuntu 러너에서도 돌고 맥에서 테스트로도 돈다.
  python3 -c 'import sys,datetime as d;print(int(d.datetime.strptime(sys.argv[1],"%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=d.timezone.utc).timestamp()))' "$1" 2>/dev/null
}

problems=0

for spec in "$@"; do
  wf="${spec%%:*}"
  mode="${spec##*:}"
  [ "$mode" = "$wf" ] && mode=success

  if [ "$mode" = "success" ]; then
    query="status=success&per_page=1"
    label="마지막 성공"
  else
    # ⚠️ **`status=completed` 를 빼지 마라.** 그냥 `per_page=1` 로 물으면 **지금 도는
    #    자기 자신**이 첫 줄로 잡혀 자기 감시가 언제나 초록이 된다. 실측으로 처음
    #    배선했을 때 정확히 그렇게 됐다 — 로컬에서 "94시간 전 🔴" 이던 것이 워크플로
    #    안에서는 "0시간 전 🟢" 으로 나왔다.
    query="status=completed&per_page=1"
    label="마지막 실행"
  fi

  last=$(gh api "repos/$REPO/actions/workflows/$wf/runs?$query" \
         --jq '.workflow_runs[0].updated_at // empty' 2>/dev/null)

  if [ -z "$last" ]; then
    echo "- \`$wf\` — **$label 이 없다.**"
    problems=1
    continue
  fi

  now=$(date -u +%s)
  then_=$(epoch "$last")
  if [ -z "$then_" ]; then
    echo "- \`$wf\` — **시각을 읽지 못했다**: \`$last\`"
    problems=1
    continue
  fi

  age=$(( (now - then_) / 3600 ))
  if [ "$age" -gt "$STALE_HOURS" ]; then
    # ⚠️ **"몇 시간 전"은 하루짜리 흔들림과 엿새째 죽은 것을 같은 얼굴로 만든다.** 이슈는
    #    본문만 갱신되고 제목은 그대로라 사람이 보는 숫자가 그것뿐인데, 기준을 막 넘긴
    #    31시간이든 엿새째든 비슷하게 읽힌다 — 국회 수집이 6일 내리 빨갛던 동안 실제로
    #    아무도 안 봤다. **몇 번을 내리 실패했는지가 그 둘을 가른다.**
    #    ⚠️ 못 세는 것이 판정을 못 하는 것이 되면 안 된다 — 실패하면 이 조각만 빠진다.
    streak=$(gh api "repos/$REPO/actions/workflows/$wf/runs?status=completed&per_page=20" \
             --jq '[.workflow_runs[].conclusion] | (index("success") // length)' 2>/dev/null) || streak=""
    [ -n "$streak" ] && streak=" · **${streak}회 내리 실패**"
    echo "- \`$wf\` — $label \`$last\` (**${age}시간 전** · 기준 ${STALE_HOURS}시간)${streak} 🔴"
    problems=1
  else
    echo "- \`$wf\` — $label \`$last\` (${age}시간 전) 🟢"
  fi
done

exit $problems
