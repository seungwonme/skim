#!/bin/bash
# 일일 RSS 피드 수집 스크립트 (cron/launchd에서 호출)

cd /Users/seungwonan/Dev/3-tool/sns_crawler
source .venv/bin/activate
python daily_feed.py >> data/daily/cron.log 2>&1

echo "------- $(date) -------" >> data/daily/cron.log
