#!/bin/bash
# 일일 피드 수집 스크립트 (cron/launchd에서 호출)
set -euo pipefail

# launchd는 셸 프로필을 읽지 않아 PATH가 /usr/bin:/bin뿐이다.
# uv, yt-dlp를 못 찾으면 크롤이 통째로 조용히 실패한다.
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

LOG_DIR="$ROOT/data/daily"
LOG="$LOG_DIR/cron.log"
mkdir -p "$LOG_DIR"

echo "======= start $(date '+%Y-%m-%d %H:%M:%S') =======" >>"$LOG"
# set -e 아래에서는 실패 즉시 죽어 종료 코드를 기록하지 못하므로 직접 받는다.
status=0
uv run skim crawl all --days 1 >>"$LOG" 2>&1 || status=$?
echo "======= end $(date '+%Y-%m-%d %H:%M:%S') exit=$status =======" >>"$LOG"
exit "$status"
