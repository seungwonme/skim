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
DOCTOR_REPORT="$LOG_DIR/doctor.txt"
mkdir -p "$LOG_DIR"

# 중복 실행 방지. 크롤이 하루를 넘기면 다음 회차와 겹쳐 같은 DB에 동시에 쓴다.
# macOS에는 flock이 없어서 mkdir의 원자성을 쓴다.
LOCK_DIR="$LOG_DIR/.run.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    lock_owner=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$lock_owner" ] && kill -0 "$lock_owner" 2>/dev/null; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] 이미 실행 중 (pid=$lock_owner). 건너뛴다." >>"$LOG"
        exit 0
    fi
    # 비정상 종료가 남긴 락은 회수한다.
    /bin/rm -rf -- "${LOCK_DIR:?}"
    mkdir "$LOCK_DIR"
fi
echo "$$" >"$LOCK_DIR/pid"
trap '/bin/rm -rf -- "${LOCK_DIR:?}"' EXIT

# 로그 로테이션. 지금까지 cron.log는 무한 append였다.
LOG_MAX_BYTES=$((10 * 1024 * 1024))
if [ -f "$LOG" ] && [ "$(wc -c <"$LOG")" -gt "$LOG_MAX_BYTES" ]; then
    mv "$LOG" "$LOG.1"
fi

# 과거분 지표 백필의 하루 몫. GeekNews는 /topic?id= 경로에 누적 요청 한도가 있어
# (2026-08-09 관측: 하루 1,000건쯤에서 403) 한 번에 다 못 받는다. 매일 조금씩 받으면
# 한도에 걸리지 않고, 다 채워지면 대상이 없어 즉시 끝난다.
METRICS_BACKFILL_LIMIT=400

echo "======= start $(date '+%Y-%m-%d %H:%M:%S') =======" >>"$LOG"

# 맥북이 배터리로 잠들어 있으면 launchd가 00:02에 깨우긴 하는데(DarkWake) Wi-Fi가
# 붙기 전에 요청이 나가 전 플랫폼이 DNS 실패로 끝난다. 2026-09-04 회차가 11개
# 플랫폼 0건으로 그렇게 죽었고, 고정 창이라 그날 분은 다음 회차에도 안 들어온다.
# 이름이 풀릴 때까지 기다리되, 영영 안 붙는 날은 실패를 기록하고 나간다.
for _ in $(seq 1 60); do
    nslookup -timeout=5 news.ycombinator.com >/dev/null 2>&1 && break
    sleep 10
done
if ! nslookup -timeout=5 news.ycombinator.com >/dev/null 2>&1; then
    echo "[!] 10분 동안 네트워크가 붙지 않아 건너뛴다" >>"$LOG"
    echo "======= end $(date '+%Y-%m-%d %H:%M:%S') exit=1 =======" >>"$LOG"
    exit 1
fi

# 이 스크립트가 도는 동안 유휴 절전을 막는다. 2026-09-05 회차는 15분마다 45초씩만
# 깨는 틈에 진행돼 14시간이 걸렸다. 뚜껑을 닫은 배터리 상태까지는 못 막는다.
caffeinate -i -w $$ &

# 크롤 전에 백업한다. 스키마 변경이 전부 in-place라 되돌릴 수단이 이것뿐이다.
# 실패해도 크롤은 계속한다 (백업이 수집을 막을 이유가 없다).
uv run skim backup --keep 3 >>"$LOG" 2>&1 || echo "[!] 백업 실패" >>"$LOG"

# --days 1을 유지한다. 발행일이 밀리는 소스(arxiv, huggingface)는 CLI의
# min_lookback_days()가 창을 알아서 넓히므로, 여기서 전역으로 넓히면 이미
# 저장된 항목까지 매일 다시 enrichment하게 된다.
# set -e 아래에서는 실패 즉시 죽어 종료 코드를 기록하지 못하므로 직접 받는다.
status=0
uv run skim crawl all --days 1 >>"$LOG" 2>&1 || status=$?

# 크롤이 실패해도 백필은 돌린다. 둘은 서로 독립이다.
backfill_status=0
uv run python scripts/backfill_feed_metrics.py --limit "$METRICS_BACKFILL_LIMIT" \
    >>"$LOG" 2>&1 || backfill_status=$?
echo "지표 백필 exit=$backfill_status" >>"$LOG"

# 점검 결과는 cron.log에 묻히지 않게 따로 떨군다. 이 파일만 보면 어제 상태를 안다.
doctor_status=0
uv run skim doctor --strict >"$DOCTOR_REPORT" 2>&1 || doctor_status=$?
cat "$DOCTOR_REPORT" >>"$LOG"
echo "doctor exit=$doctor_status (상세: $DOCTOR_REPORT)" >>"$LOG"

echo "======= end $(date '+%Y-%m-%d %H:%M:%S') exit=$status =======" >>"$LOG"
exit "$status"
