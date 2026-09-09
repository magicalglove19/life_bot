#!/bin/bash
# GitHub 워크플로를 지금 즉시 실행시킨다 (repository_dispatch).
#
# 왜 필요한가 — GitHub의 예약(schedule) 실행은 이 레포 실측으로 2~4.6시간씩 밀린다.
# 이벤트 트리거는 몇 초 안에 실행되므로, 시각이 중요한 작업은 이 스크립트로 찌른다.
#
# 사용:
#   ./trigger.sh judoju 1200      # 주도주 12:00 슬롯
#   ./trigger.sh ma8-screener     # 유목민 스크리너
#
# 토큰은 맥 키체인에서 읽는다. 파일이나 셸 기록에 남지 않는다.
#   보관:  security add-generic-password -a "$USER" -s life-bot-github-pat -w
#   삭제:  security delete-generic-password -a "$USER" -s life-bot-github-pat

set -euo pipefail

REPO="magicalglove19/life_bot"
KEYCHAIN_SERVICE="life-bot-github-pat"

EVENT="${1:-}"
SLOT="${2:-}"

if [ -z "$EVENT" ]; then
  echo "사용법: $0 <워크플로 이벤트명> [슬롯]" >&2
  echo "  예: $0 judoju 1200" >&2
  exit 2
fi

TOKEN="$(security find-generic-password -a "$USER" -s "$KEYCHAIN_SERVICE" -w 2>/dev/null || true)"
if [ -z "$TOKEN" ]; then
  echo "❌ 키체인에 토큰이 없습니다. 아래 명령으로 먼저 저장하세요:" >&2
  echo "   security add-generic-password -a \"\$USER\" -s $KEYCHAIN_SERVICE -w" >&2
  exit 1
fi

if [ -n "$SLOT" ]; then
  PAYLOAD="{\"event_type\":\"$EVENT\",\"client_payload\":{\"slot\":\"$SLOT\"}}"
else
  PAYLOAD="{\"event_type\":\"$EVENT\"}"
fi

CODE="$(curl -sS -o /tmp/trigger_resp.txt -w '%{http_code}' \
  -X POST "https://api.github.com/repos/$REPO/dispatches" \
  -H "Accept: application/vnd.github+json" \
  -H "Authorization: Bearer $TOKEN" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -d "$PAYLOAD")"

STAMP="$(date '+%Y-%m-%d %H:%M:%S')"
if [ "$CODE" = "204" ]; then
  echo "$STAMP  ✅ $EVENT${SLOT:+ ($SLOT)} 실행 요청 완료"
else
  echo "$STAMP  ❌ $EVENT${SLOT:+ ($SLOT)} 실패 — HTTP $CODE" >&2
  head -c 300 /tmp/trigger_resp.txt >&2; echo >&2
  case "$CODE" in
    401) echo "   → 토큰이 잘못됐거나 만료됐습니다. 키체인의 값을 갱신하세요." >&2 ;;
    403) echo "   → 권한 부족. 토큰에 이 레포의 Contents 쓰기 권한이 있어야 합니다." >&2 ;;
    404) echo "   → 레포를 못 찾거나 토큰에 접근 권한이 없습니다." >&2 ;;
  esac
  exit 1
fi
