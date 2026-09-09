#!/bin/bash
# 평일 12:00에 judoju 1200 슬롯을 실행시키는 맥 예약(launchd)을 설치한다.
#
# GitHub 예약으로는 10:00~13:20 KST 구간에 실행되게 만들 수 없다(예약+지연 조합에
# 해가 없음). 그래서 이 시간대만 맥이 직접 GitHub을 찌른다.
#
# 실행:  ./scripts/local/install-judoju-trigger.sh
# 해제:  launchctl unload ~/Library/LaunchAgents/com.lifebot.judoju-1200.plist

set -euo pipefail

LABEL="com.lifebot.judoju-1200"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
SCRIPT="$(cd "$(dirname "$0")" && pwd)/trigger.sh"
LOG_DIR="$HOME/Library/Logs"

[ -x "$SCRIPT" ] || { echo "❌ trigger.sh 를 찾을 수 없습니다: $SCRIPT" >&2; exit 1; }

if ! security find-generic-password -a "$USER" -s life-bot-github-pat -w >/dev/null 2>&1; then
  echo "⚠️  키체인에 토큰이 아직 없습니다. 예약은 설치하지만, 먼저 아래를 실행하세요:"
  echo "     security add-generic-password -a \"\$USER\" -s life-bot-github-pat -w"
  echo
fi

mkdir -p "$HOME/Library/LaunchAgents" "$LOG_DIR"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$SCRIPT</string>
    <string>judoju</string>
    <string>1200</string>
  </array>
  <!-- 월~금 12:00. 그 시각에 맥이 자고 있었다면 깨어날 때 실행되고,
       13:30(judoju의 1200 슬롯 허용 한계)을 넘겼다면 judoju.py가 스스로 건너뛴다. -->
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Weekday</key><integer>1</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>2</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>3</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>4</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
    <dict><key>Weekday</key><integer>5</integer><key>Hour</key><integer>12</integer><key>Minute</key><integer>0</integer></dict>
  </array>
  <key>StandardOutPath</key><string>$LOG_DIR/lifebot-trigger.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/lifebot-trigger.log</string>
</dict>
</plist>
PLIST_EOF

launchctl unload "$PLIST" 2>/dev/null || true
launchctl load "$PLIST"

echo "✅ 설치 완료"
echo "   예약:   월~금 12:00"
echo "   로그:   $LOG_DIR/lifebot-trigger.log"
echo "   해제:   launchctl unload $PLIST"
echo
echo "지금 바로 시험해보려면:  $SCRIPT judoju 1200"
