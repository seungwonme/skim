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

# 과거분 지표 백필의 하루 몫. GeekNews는 /topic?id= 경로에 누적 요청 한도가 있어
# (2026-08-09 관측: 하루 1,000건쯤에서 403) 한 번에 다 못 받는다. 매일 조금씩 받으면
# 한도에 걸리지 않고, 다 채워지면 대상이 없어 즉시 끝난다.
METRICS_BACKFILL_LIMIT=400

echo "======= start $(date '+%Y-%m-%d %H:%M:%S') =======" >>"$LOG"
# set -e 아래에서는 실패 즉시 죽어 종료 코드를 기록하지 못하므로 직접 받는다.
status=0
uv run skim crawl all --days 1 >>"$LOG" 2>&1 || status=$?

# 크롤이 실패해도 백필은 돌린다. 둘은 서로 독립이다.
backfill_status=0
uv run python scripts/backfill_feed_metrics.py --limit "$METRICS_BACKFILL_LIMIT" \
    >>"$LOG" 2>&1 || backfill_status=$?
echo "지표 백필 exit=$backfill_status" >>"$LOG"

echo "======= end $(date '+%Y-%m-%d %H:%M:%S') exit=$status =======" >>"$LOG"
exit "$status"
