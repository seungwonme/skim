# Threads 크롤러 구현 문서

## 아키텍처

```
사용자 → python main.py login → Chrome (CDP) → 쿠키 추출 → session.json
사용자 → python main.py threads → HTTP API (requests) → JSON 저장
```

- **인증**: Chrome DevTools Protocol (CDP)로 쿠키만 추출. 브라우저는 로그인 시에만 사용.
- **데이터 수집**: Instagram Private API (`i.instagram.com`)에 직접 HTTP 요청. 브라우저 없음.
- **세션 관리**: `data/sessions/threads_session.json`에 쿠키 저장.

## API 엔드포인트

| 엔드포인트 | 메서드 | 설명 |
|---|---|---|
| `/api/v1/feed/text_post_app_timeline/` | **POST** | For You 타임라인 피드 |
| `/api/v1/text_feed/{userID}/profile/` | GET | 특정 사용자 프로필 피드 |

Base URL: `https://i.instagram.com`

### 공통 헤더

```
User-Agent: Barcelona 289.0.0.77.109 Android
X-IG-App-ID: 238260118697367
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
```

### 타임라인 피드 (For You)

```
POST /api/v1/feed/text_post_app_timeline/
Body: pagination_source=text_post_feed_threads&max_id={cursor}
```

응답 구조:
```json
{
  "feed_items": [
    {
      "text_post_app_thread": {
        "thread_items": [
          {
            "post": {
              "user": {"username": "...", "full_name": "..."},
              "caption": {"text": "..."},
              "like_count": 100,
              "text_post_app_info": {
                "direct_reply_count": 10,
                "repost_count": 5,
                "quote_count": 2
              },
              "code": "POST_CODE",
              "taken_at": 1774203064
            }
          }
        ]
      }
    }
  ],
  "next_max_id": "...",
  "more_available": true
}
```

### 사용자 프로필 피드

```
GET /api/v1/text_feed/{userID}/profile/?max_id={cursor}
```

응답 구조:
```json
{
  "threads": [
    {
      "thread_items": [
        {"post": { ... }}
      ]
    }
  ],
  "next_max_id": "..."
}
```

## 사용법

```bash
# 1. 최초 로그인 (Chrome이 열리면 수동 로그인)
python main.py login

# 2. For You 타임라인 크롤링
python main.py threads --count 10

# 3. 특정 사용자 피드 크롤링
python main.py threads --user-id 314216 --count 5

# 4. 디버그 모드
python main.py threads --count 3 --debug

# 5. Google Sheets 저장
python main.py threads --count 10 --sheets
```

## 스레드 내용 합치기

Threads에서 하나의 스레드는 작성자의 self-reply chain으로 구성됩니다.
API 응답의 `thread_items` 배열에 이 체인이 포함되어 있으며,
같은 작성자의 연속된 아이템의 content를 `\n\n---\n\n` 구분자로 합쳐 하나의 Post로 반환합니다.

## 트러블슈팅 기록

### GET vs POST 문제

타임라인 피드 엔드포인트(`/feed/text_post_app_timeline/`)는 **POST 요청**이 필요합니다.
GET으로 요청하면 `405 Method Not Allowed`가 반환됩니다.

```
GET  → 405 (실패)
POST → 200 (성공) + pagination_source=text_post_feed_threads 필수
```

이 사실은 `junhoyeo/threads-api`(TypeScript)와 `Danie1/threads-api`(Python) 레포에서 확인했습니다.

### GraphQL doc_id 만료

초기에 Threads 웹 GraphQL API(`threads.net/api/graphql`)를 시도했으나,
`doc_id` 값들이 2023년 이후 변경되어 `data: null`을 반환했습니다.

```
doc_id: 23996318473300828 (BarcelonaProfileRootQuery) → data: null
doc_id: 6232751443445612 (BarcelonaProfileThreadsTabQuery) → data: null
```

→ Instagram Private API(`i.instagram.com`)로 전환하여 해결.

### Bearer 토큰 불필요

기존 리버스 엔지니어링 레포들은 `Authorization: Bearer IGT:2:{token}` 헤더를 사용했지만,
테스트 결과 **세션 쿠키만으로도 API 호출이 가능**했습니다.
이는 구현을 크게 단순화합니다 — Bloks 인증 프로토콜을 구현할 필요 없이 쿠키만 추출하면 됩니다.

### HTML 내장 데이터

`threads.net/@{username}` 페이지 HTML에는 `<script type="application/json">` 태그로
Relay 프리로드 데이터가 포함되어 있습니다 (~96KB). 하지만 이를 파싱하는 것보다
Instagram Private API를 직접 호출하는 것이 더 안정적이고 단순합니다.

### 참고 레포 (모두 Archived)

- [junhoyeo/threads-api](https://github.com/junhoyeo/threads-api) — TypeScript, 1.6k stars
- [Danie1/threads-api](https://github.com/Danie1/threads-api) — Python, 143 stars
- [dmytrostriletskyi/threads-net](https://github.com/dmytrostriletskyi/threads-net) — Python, 423 stars (코드 삭제됨)
- [m1guelpf/threads-re](https://github.com/m1guelpf/threads-re) — RE 문서

모두 2023년 Meta의 cease & desist 요청으로 아카이브됨.
