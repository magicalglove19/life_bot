#!/bin/bash
# judoju 1200 슬롯 예약 설치 — 범용 설치기로 넘긴다.
#
# 예전 버전은 이 레포(~/Desktop 아래)의 trigger.sh 를 launchd 가 직접 실행했는데,
# macOS TCC 가 에이전트의 Desktop 접근을 막아 "Operation not permitted"(exit 126)로
# 조용히 실패했다. 지금은 보호 구역 밖에 복사해 두고 그 사본을 실행한다.
#
# 실행:  ./scripts/local/install-judoju-trigger.sh
# 해제:  ./scripts/local/install-trigger.sh judoju 12:00 --uninstall

exec "$(cd "$(dirname "$0")" && pwd)/install-trigger.sh" judoju 12:00 1200
