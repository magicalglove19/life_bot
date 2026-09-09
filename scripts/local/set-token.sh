#!/bin/bash
# GitHub 토큰을 맥 키체인에 저장한다. 저장 전에 실제로 GitHub에 물어보고,
# 유효할 때만 저장한다 (무효한 값이 저장돼 나중에 401로 헤매는 걸 막는다).
#
# 사용 (셋 중 아무거나):
#   ./scripts/local/set-token.sh github_pat_xxx   ← 토큰을 인자로
#   ./scripts/local/set-token.sh                  ← 클립보드에 있으면 자동
#   ./scripts/local/set-token.sh                  ← 없으면 직접 입력하라고 물어본다

set -euo pipefail
SERVICE="life-bot-github-pat"
REPO="magicalglove19/life_bot"

strip() { printf '%s' "${1//[$'\t\r\n ']/}"; }
looks_like_token() {
  case "$1" in github_pat_*|ghp_*) return 0 ;; *) return 1 ;; esac
}

# 1순위: 명령 인자   2순위: 클립보드   3순위: 직접 입력
TOKEN="$(strip "${1:-}")"
SRC="인자"

if ! looks_like_token "$TOKEN"; then
  TOKEN="$(strip "$(pbpaste 2>/dev/null || true)")"
  SRC="클립보드"
fi

if ! looks_like_token "$TOKEN"; then
  echo "클립보드에 토큰이 없습니다. 직접 붙여넣으세요."
  echo "(이 칸에서는 붙여넣기가 화면에 보입니다. Cmd+V 후 엔터)"
  printf '토큰: '
  read -r RAW
  TOKEN="$(strip "$RAW")"
  SRC="직접 입력"
fi

if [ -z "$TOKEN" ]; then
  echo "❌ 아무것도 입력되지 않았습니다." >&2; exit 1
fi

echo "${SRC}에서 읽음: ${#TOKEN}자"
if looks_like_token "$TOKEN"; then
  case "$TOKEN" in
    github_pat_*) echo "  형식: fine-grained ✅" ;;
    ghp_*)        echo "  형식: classic ✅" ;;
  esac
else
  echo "❌ GitHub 토큰 형식이 아닙니다 (github_pat_ 또는 ghp_ 로 시작해야 함)." >&2
  echo "   읽은 값의 앞 12자: ${TOKEN:0:12}..." >&2
  exit 1
fi

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
