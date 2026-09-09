#!/bin/bash
# ════════════════════════════════════════════════════════════════════
#  미너비니 스크리너 → 텔레그램  (더블클릭 실행)
#
#  S&P 500을 스캔해서 결과를 텔레그램으로 보냅니다.
#  매일 아침 05:30 KST에 GitHub Actions가 자동으로 보내는 것과 같은 내용이며,
#  이 파일은 "지금 당장" 받아보고 싶을 때 씁니다.
#
#  처음 실행하면 텔레그램 봇 토큰과 채팅 ID를 한 번 물어보고
#  맥 키체인에 저장합니다. 그 뒤로는 그냥 더블클릭만 하면 됩니다.
#
#  보내지 않고 내용만 확인하려면 터미널에서:
#      python3 scripts/minervini_report.py --dry-run
# ════════════════════════════════════════════════════════════════════

cd "$(dirname "$0")" || exit 1
export PYTHONIOENCODING=utf-8
export LANG="${LANG:-en_US.UTF-8}"

printf '\033[8;45;120t'
clear 2>/dev/null || printf '\033[2J\033[H'

B=$'\033[1m'; D=$'\033[2m'; G=$'\033[38;5;41m'; Y=$'\033[38;5;221m'
R=$'\033[38;5;203m'; C=$'\033[38;5;44m'; O=$'\033[38;5;208m'; N=$'\033[0m'

SVC_TOKEN="life-bot-telegram-token"
SVC_CHAT="life-bot-telegram-chat"

echo "${O}${B}"
echo "   📈  미너비니 스크리너 → 텔레그램"
echo "${N}${D}   S&P 500 · Trend Template + VCP · 결과를 텔레그램으로 발송${N}"
echo

finish() {
    echo
    echo "${D}────────────────────────────────────────────────────────────${N}"
    read -r -p "엔터를 누르면 창이 닫힙니다..." _
    exit "${1:-0}"
}

# ── 파이썬 ──────────────────────────────────────────────────
PY=""
for cand in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    command -v "$cand" >/dev/null 2>&1 && { PY="$cand"; break; }
done
if [ -z "$PY" ]; then
    echo "${R}파이썬3을 찾지 못했습니다.  brew install python3 로 설치하세요.${N}"; finish 1
fi

# ── 라이브러리 ──────────────────────────────────────────────
if ! "$PY" -c "import yfinance, pandas, numpy, requests, lxml" >/dev/null 2>&1; then
    echo "${Y}필요한 라이브러리가 없습니다.${N}"
    read -r -p "지금 설치할까요? (y/n) " yn
    case "$yn" in
        [Yy]*) "$PY" -m pip install --user -r requirements.txt || finish 1 ;;
        *) finish 1 ;;
    esac
fi

# ── 텔레그램 자격증명 (맥 키체인) ────────────────────────────
kc_get() { security find-generic-password -a "$USER" -s "$1" -w 2>/dev/null; }
kc_set() {
    while security delete-generic-password -a "$USER" -s "$1" >/dev/null 2>&1; do :; done
    security add-generic-password -a "$USER" -s "$1" -w "$2" -U
}

TOKEN="$(kc_get "$SVC_TOKEN")"
CHAT="$(kc_get "$SVC_CHAT")"

if [ -z "$TOKEN" ] || [ -z "$CHAT" ]; then
    echo "${Y}텔레그램 설정이 아직 없습니다. 한 번만 등록하면 됩니다.${N}"
    echo "${D}  · 봇 토큰   : @BotFather 에서 받은 값 (예: 1234567:AAE...)${N}"
    echo "${D}  · 채팅 ID   : @userinfobot 에게 아무 말이나 보내면 알려줍니다${N}"
    echo "${D}  · GitHub Secrets 에 넣어둔 값과 같은 것을 쓰면 됩니다${N}"
    echo

    if [ -z "$TOKEN" ]; then
        printf '봇 토큰: '
        read -r TOKEN
        TOKEN="${TOKEN//[[:space:]]/}"
    fi
    if [ -z "$CHAT" ]; then
        printf '채팅 ID: '
        read -r CHAT
        CHAT="${CHAT//[[:space:]]/}"
    fi
    [ -z "$TOKEN" ] || [ -z "$CHAT" ] && { echo "${R}값이 비어 있습니다.${N}"; finish 1; }

    echo -n "텔레그램에 확인 중... "
    NAME="$("$PY" - "$TOKEN" <<'PYEOF'
import json, sys, urllib.request
try:
    with urllib.request.urlopen(f"https://api.telegram.org/bot{sys.argv[1]}/getMe", timeout=15) as r:
        d = json.load(r)
    print(d["result"]["username"] if d.get("ok") else "")
except Exception:
    print("")
PYEOF
)"
    if [ -z "$NAME" ]; then
        echo "실패"
        echo "${R}❌ 텔레그램이 이 토큰을 거부했습니다. 저장하지 않았습니다.${N}"; finish 1
    fi
    echo "OK (@${NAME})"
    kc_set "$SVC_TOKEN" "$TOKEN" >/dev/null
    kc_set "$SVC_CHAT" "$CHAT" >/dev/null
    echo "${G}✅ 키체인에 저장했습니다. 다음부터는 더블클릭만 하면 됩니다.${N}"
    echo
fi

export TELEGRAM_BOT_TOKEN="$TOKEN"
export TELEGRAM_CHAT_ID="$CHAT"

# ── 실행 ────────────────────────────────────────────────────
echo "${C}스캔을 시작합니다. 처음 실행은 1분쯤 걸립니다.${N}"
echo
"$PY" scripts/minervini_report.py --verbose
CODE=$?

echo
if [ $CODE -eq 0 ]; then
    echo "${G}${B}✅ 텔레그램으로 보냈습니다.${N} ${D}(1편·2편 두 통)${N}"
else
    echo "${R}${B}❌ 전송에 실패했습니다.${N} ${D}위 로그를 확인하세요.${N}"
fi
finish $CODE
