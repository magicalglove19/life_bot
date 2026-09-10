#!/bin/bash
# 워크플로를 정해진 시각에 repository_dispatch 로 찌르는 맥 예약(launchd)을 설치한다.
#
#   ./scripts/local/install-trigger.sh kcg 15:00
#   ./scripts/local/install-trigger.sh judoju 12:00 1200
#   ./scripts/local/install-trigger.sh kcg 15:00 --uninstall
#
# ⚠️ trigger.sh 를 이 레포(~/Desktop 아래)에서 직접 실행하면 안 된다.
#    macOS TCC 가 launchd 에이전트의 Desktop 접근을 막아서
#    "Operation not permitted" (exit 126) 로 조용히 실패한다.
#    그래서 보호 구역 밖(~/Library/Application Support/lifebot)에 복사해 두고
#    launchd 는 그 사본을 실행한다. 이 스크립트를 다시 돌리면 사본도 갱신된다.

set -euo pipefail

EVENT="${1:-}"
AT="${2:-}"
SLOT=""
UNINSTALL=0
for arg in "${@:3}"; do
  case "$arg" in
    --uninstall) UNINSTALL=1 ;;
    *) SLOT="$arg" ;;
  esac
done

if [ -z "$EVENT" ] || [ -z "$AT" ]; then
  echo "사용법: $0 <이벤트명> <HH:MM> [슬롯] [--uninstall]" >&2
  echo "  예: $0 kcg 15:00" >&2
  echo "      $0 judoju 12:00 1200" >&2
  exit 2
fi

HOUR="${AT%%:*}"; HOUR="${HOUR#0}"; HOUR="${HOUR:-0}"
MIN="${AT##*:}";  MIN="${MIN#0}";   MIN="${MIN:-0}"

LABEL="com.lifebot.${EVENT}-$(printf '%02d%02d' "$HOUR" "$MIN")"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
STAGE="$HOME/Library/Application Support/lifebot"
STAGED="$STAGE/trigger.sh"
SRC="$(cd "$(dirname "$0")" && pwd)/trigger.sh"
LOG="$HOME/Library/Logs/lifebot-trigger.log"

if [ "$UNINSTALL" -eq 1 ]; then
  launchctl unload "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "✅ 해제했습니다: $LABEL"
  exit 0
fi

[ -f "$SRC" ] || { echo "❌ trigger.sh 를 찾을 수 없습니다: $SRC" >&2; exit 1; }

if ! security find-generic-password -a "$USER" -s life-bot-github-pat -w >/dev/null 2>&1; then
  echo "⚠️  키체인에 GitHub 토큰이 없습니다. 예약은 설치하지만 먼저 아래를 실행하세요:"
  echo "     ./scripts/local/set-token.sh"
  echo
fi

mkdir -p "$STAGE" "$HOME/Library/LaunchAgents" "$(dirname "$LOG")"
cp "$SRC" "$STAGED"
chmod +x "$STAGED"

cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$STAGED</string>
    <string>$EVENT</string>$([ -n "$SLOT" ] && printf '\n    <string>%s</string>' "$SLOT")
  </array>
  <!-- 월~금 $AT. 그 시각에 맥이 자고 있었다면 깨어날 때 실행되고,
       늦게 도착한 실행은 워크플로 쪽 시간대 가드가 걸러낸다. -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
PLIST_EOF

plutil -lint "$PLIST" >/dev/null || { echo "❌ plist 생성 실패" >&2; exit 1; }
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ 설치 완료: $LABEL"
echo "   예약:   월~금 $AT  → repository_dispatch \"$EVENT\"${SLOT:+ (슬롯 $SLOT)}"
echo "   실행기: $STAGED"
echo "   로그:   $LOG"
echo "   해제:   $0 $EVENT $AT --uninstall"
echo
echo "지금 바로 시험:  \"$STAGED\" $EVENT $SLOT"
