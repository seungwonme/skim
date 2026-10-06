# 상태 점검

"잘 돌고 있어?", "데일리 괜찮아?" 같은 질문에 답하는 절차와 판정 기준. 데일리가 무엇을 어디에 남기는지는 `AGENTS.md`의 `## 데일리 운영`이 정본이다.

## 절차

1. `uv run skim doctor --strict --runs 10`을 돌린다. exit 0이면 doctor가 아는 경고는 없다. 회차 경고는 최근 7일 안에서 뒤에 `success`가 오지 않은 회차만 내고, 복구된 회차는 `recovered: #N <상태> -> #M success` 줄로 보인다. 출력 시각은 로컬 시각이다.
2. `success`가 아닌 회차는 원인을 로그에서 읽는다. `grep -n '======= \(start\|end\)' data/daily/cron.log | tail -20`으로 구간을 찾고, 구간 안의 `(run #N)` 줄로 DB 회차 번호를 맞춘 뒤 `[!]` 줄을 본다.
3. `recovered:` 줄이 없는 비성공 회차는 아직 복구되지 않은 것이다. feed 플랫폼은 다음 회차의 `--catch-up`이 놓친 날을 채운다. SNS(threads, x, linkedin, reddit)는 최근 N건만 받아서 못 받은 날을 되찾지 못한다.
4. 날짜별 유입은 `uv run skim coverage --by-day --days 7`로 본다. 로컬 날짜 기준이고, 수집이 통째로 빠진 날도 0으로 보인다.
5. 아래 두 표로 실제 문제와 정상 경고를 나눈다. 표에 없는 `실패 N` 류 경고는 판정 전에 그 숫자가 무엇을 세는지 코드에서 확인한다. 요청 실패와 빈 결과를 같은 실패로 세던 카운터가 있었다.

## 실제 문제

| 신호 | 볼 곳 |
|---|---|
| `doctor --strict` exit 1 | 출력의 warning 줄 |
| 회차 `failed` 또는 `degraded` | `cron.log`의 그 회차 구간. 원인이 아래 정상 경고에 해당하면 복구 여부만 보고한다 |
| 회차 `interrupted` | 프로세스가 중간에 죽었다. `current_platform`이 중단 지점이고, `cron.log`에서 `end` 줄 없는 구간의 마지막 줄을 본다. 거기에 원인이 없으면 `pmset -g log`에서 그 시각의 재시작이나 종료를 본다 |
| run summary의 `0건 회귀: <platform>` | 판정 기준은 `AGENTS.md`의 `#### 0건을 회귀로 볼 때` |
| LinkedIn, Reddit의 `댓글 수집 실패 N건` | 실제 요청, 파싱 실패만 센다. 여러 건이면 세션 만료나 네트워크를 의심하고, 같은 회차 다른 플랫폼의 `[!]` 줄과 `sessions:`를 본다 |
| `[!] hackernews/show: Algolia 보충 실패` (또는 `/ask`) | 그 회차는 피드 상한 밖의 고득점 글이 빠졌을 수 있다. 보고에 적는다 |
| `sessions:`에서 SNS 세션이 빠짐 | `data/sessions/`에 그 플랫폼 파일이 있는지 본다. 재로그인(`uv run skim login <platform>`)은 사용자에게 확인받고 한다 |
| GeekNews "피드 요약 조각뿐인 본문" 경고, 한도 파일의 차단 기록 | `AGENTS.md`의 "요청량으로 막히는 호스트" 항목 |

## 정상 경고

doctor가 경고로 올리지 않았다면 아래는 그 자체로 문제가 아니다.

| 보이는 것 | 이유 |
|---|---|
| GeekNews `partial`이 최근 7일의 절반을 넘음 | 하루 글 수가 토픽 요청 한도보다 많다. 원문은 붙어 있다 |
| youtube `missing_text`가 큼 | `youtube-history` 백필 행은 사용자가 요청할 때만 자막을 채운다 |
| hackernews 본문 누락 몇 건 | 원문 사이트가 401, 402, 403을 주거나 PDF 다운로드로 끝난다 |
| `hnrss ... Algolia로 폴백합니다` | hnrss 장애를 Algolia가 메웠다 |
| youtube의 `RSS 실패 N` | YouTube RSS가 실제로 실패해 그 채널은 yt-dlp로 받았다. 같은 줄의 `새 영상 없음`은 실패가 아니다 |
| `피드 상한 30건에 닿았습니다. N점 이상 M건을 Algolia로 보충했습니다` | 상한 밖의 고득점 글은 Algolia가 채웠다. 문턱 미만의 오래된 저점수 글만 빠진다 |
| LinkedIn, Reddit의 `보여줄 댓글 없음 N건` | 댓글이 삭제되거나 숨겨진 글이다. 요청은 성공했다 |
| `NameResolutionError`로 한 회차 실패, 다음 회차 성공 | 회차 중 네트워크가 끊겼고 catch-up이 채웠다 |
| `start`와 `end` 사이가 몇 시간 | 맥이 잠든 채 DarkWake 때만 진행했다. `pmset -g log`의 Sleep, DarkWake 줄로 확인한다 |

## 보고

1. 첫 줄에 결론(정상, 문제 있음)을 쓴다.
2. 실제 문제는 회차 번호, 원인 로그 줄, 복구 여부를 함께 쓴다.
3. 정상 경고는 항목마다 이유 한 줄만 쓴다.
4. 두 표 어디에도 없는 경고는 "따로 볼 것"으로 분리하고, 근거 로그 줄을 붙인다.
5. 확인하지 않은 것(테스트, 데스크톱 앱)을 밝힌다.
