# 상태 점검

"잘 돌고 있어?", "데일리 괜찮아?" 같은 질문에 답하는 절차와 판정 기준. 데일리가 무엇을 어디에 남기는지는 `AGENTS.md`의 `## 데일리 운영`이 정본이다.

## 절차

1. `uv run skim doctor --strict`를 돌린다. exit 0이면 doctor가 아는 경고는 없다. 단 회차 경고는 최근 3회차만 보므로, 그 앞 회차는 2단계에서 따로 본다.
2. doctor의 `recent runs`는 최근 5회차뿐이다. 지난 일주일을 보려면 `runs` 테이블을 직접 읽는다.

```sql
SELECT id, status, datetime(started_at, 'localtime') AS started, current_platform, summary
FROM runs ORDER BY id DESC LIMIT 10;
```

3. `success`가 아닌 회차는 원인을 로그에서 읽는다. `grep -n '======= \(start\|end\)' data/daily/cron.log | tail -20`으로 구간을 찾고, 구간 안의 `(run #N)` 줄로 DB 회차 번호를 맞춘 뒤 `[!]` 줄을 본다.
4. 실패한 회차 다음 회차가 `success`인지 본다. feed 플랫폼은 `--catch-up`이 놓친 날을 채운다. SNS(threads, x, linkedin, reddit)는 최근 N건만 받아서 못 받은 날을 되찾지 못한다.
5. 날짜별 유입이 필요하면 `posts.crawled_at`을 로컬 날짜로 바꿔 묶는다. `skim coverage`는 창 전체 합계만 준다.

```sql
SELECT platform, date(crawled_at, 'localtime') AS day, count(*) AS new_rows
FROM posts WHERE crawled_at >= datetime('now', '-7 days')
GROUP BY platform, day ORDER BY platform, day;
```

6. 아래 두 표로 실제 문제와 정상 경고를 나눈다.

SQL은 `sqlite3 data/skim.db "<SQL>"`로 돌린다. `-readonly`나 `mode=ro`는 WAL 보조 파일(`-shm`)이 없으면 열리지 않을 수 있다. SELECT만 하면 크롤 중에도 쓰기를 막지 않는다.

## 실제 문제

| 신호 | 원인을 찾을 곳 |
|---|---|
| `doctor --strict` exit 1 | 출력의 warning 줄 |
| 회차 `failed` 또는 `degraded` | `cron.log`의 그 회차 구간. 원인이 아래 정상 경고에 해당하면 복구 여부만 보고한다 |
| 회차 `interrupted` | 프로세스가 중간에 죽었다. `current_platform`이 중단 지점이고, `cron.log`에서 `end` 줄 없는 구간의 마지막 줄을 본다. 거기에 원인이 없으면 `pmset -g log`에서 그 시각의 재시작이나 종료를 본다 |
| run summary의 `0건 회귀: <platform>` | 판정 기준은 `AGENTS.md`의 `#### 0건을 회귀로 볼 때` |
| `sessions:`에서 SNS 세션이 빠짐 | `uv run skim login <platform>` |
| GeekNews "피드 요약 조각뿐인 본문" 경고, 한도 파일의 차단 기록 | `AGENTS.md`의 "요청량으로 막히는 호스트" 항목 |

## 정상 경고

doctor가 경고로 올리지 않았다면 아래는 그 자체로 문제가 아니다.

| 보이는 것 | 이유 |
|---|---|
| GeekNews `partial`이 최근 7일의 절반을 넘음 | 하루 글 수가 토픽 요청 한도보다 많다. 원문은 붙어 있다 |
| youtube `missing_text`가 큼 | `youtube-history` 백필 행은 사용자가 요청할 때만 자막을 채운다 |
| hackernews 본문 누락 몇 건 | 원문 사이트가 403, 402를 주거나 PDF 다운로드로 끝난다 |
| `hnrss ... Algolia로 폴백합니다` | hnrss 장애를 Algolia가 메웠다 |
| youtube의 `RSS 실패 N` | 창 안에 새 영상이 없는 채널도 실패로 센다. 실제 피드 실패 수가 아니다 |
| `NameResolutionError`로 한 회차 실패, 다음 회차 성공 | 회차 중 네트워크가 끊겼고 catch-up이 채웠다 |
| `start`와 `end` 사이가 몇 시간 | 맥이 잠든 채 DarkWake 때만 진행했다. `pmset -g log`의 Sleep, DarkWake 줄로 확인한다 |

## 보고

1. 첫 줄에 결론(정상, 문제 있음)을 쓴다.
2. 실제 문제는 회차 번호, 원인 로그 줄, 복구 여부를 함께 쓴다.
3. 정상 경고는 항목마다 이유 한 줄만 쓴다.
4. 두 표 어디에도 없는 경고는 "따로 볼 것"으로 분리하고, 근거 로그 줄을 붙인다.
5. 확인하지 않은 것(테스트, 데스크톱 앱)을 밝힌다.
