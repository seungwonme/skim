#!/bin/bash
# 일일 피드 수집 스크립트 (cron/launchd에서 호출)

cd /Users/seungwonan/Dev/3-tool/skim
uv run python main.py crawl all --days 1 >> data/daily/cron.log 2>&1

echo "------- $(date) -------" >> data/daily/cron.log
