#!/bin/bash
# GitHub 토큰을 맥 키체인에 저장한다. 저장 전에 실제로 GitHub에 물어보고,
# 유효할 때만 저장한다 (무효한 값이 저장돼 나중에 401로 헤매는 걸 막는다).
#
# 사용:
#   1) GitHub에서 토큰을 복사한다 (클립보드에 둔 채로)
#   2) ./scripts/local/set-token.sh

set -euo pipefail
SERVICE="life-bot-github-pat"
REPO="magicalglove19/life_bot"

TOKEN="$(pbpaste)"
TOKEN="${TOKEN//[$'\t\r\n ']/}"          # 눈에 안 보이는 공백·줄바꿈 제거

if [ -z "$TOKEN" ]; then
  echo "❌ 클립보드가 비어 있습니다. GitHub에서 토큰을 복사한 뒤 다시 실행하세요." >&2
  exit 1
fi

echo "클립보드에서 읽음: ${#TOKEN}자"
case "$TOKEN" in
  github_pat_*) echo "  형식: fine-grained ✅" ;;
  ghp_*)        echo "  형식: classic ✅" ;;
  *)            echo "❌ GitHub 토큰이 아닙니다 (github_pat_ 또는 ghp_ 로 시작해야 함)." >&2
                echo "   복사가 제대로 됐는지 확인하세요." >&2; exit 1 ;;
esac

echo -n "GitHub에 유효성 확인 중... "
CODE="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" https://api.github.com/user)"
if [ "$CODE" != "200" ]; then
  echo "실패 (HTTP $CODE)"
  echo "❌ GitHub이 이 토큰을 거부했습니다. 저장하지 않았습니다." >&2
  [ "$CODE" = "401" ] && echo "   → 만료됐거나, 복사가 잘렸거나, 이미 재발급된 토큰입니다." >&2
  exit 1
fi
echo "OK"

echo -n "레포 접근 권한 확인 중... "
CODE="$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer $TOKEN" "https://api.github.com/repos/$REPO")"
if [ "$CODE" = "200" ]; then
  echo "OK"
else
  echo "HTTP $CODE"
  echo "⚠️  토큰은 유효하지만 $REPO 에 접근하지 못합니다." >&2
  echo "   토큰 설정에서 Repository access → Only select repositories → life_bot 인지," >&2
  echo "   Permissions → Contents: Read and write 인지 확인하세요." >&2
  echo "   (일단 저장은 합니다. 권한만 고치면 재저장 없이 동작합니다.)" >&2
fi

# 기존 항목을 모두 지우고 하나만 남긴다 (중복되면 엉뚱한 값이 먼저 읽힌다)
while security delete-generic-password -a "$USER" -s "$SERVICE" >/dev/null 2>&1; do :; done
security add-generic-password -a "$USER" -s "$SERVICE" -w "$TOKEN" -U

echo "✅ 키체인에 저장 완료"
echo
echo "이제 시험해보세요:  ./scripts/local/trigger.sh judoju 1200"
