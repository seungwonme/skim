# Skim Desktop GUI Design

## Summary

`skim`에 로컬 퍼스트 데스크톱 GUI를 추가한다. 첫 릴리스는 macOS를 우선 지원하고, 구조는 Windows 확장을 고려해 설계한다. 앱의 범위는 `설정`과 `탐색`으로 제한한다. 즉, 사용자는 GUI에서 수집 대상을 등록하고 자격 증명을 관리하며, 이미 저장된 SQLite 데이터를 검색, 필터링, 상세 조회할 수 있어야 한다. 크롤링 코어와 로그인 세션 추출 로직은 기존 Python 자산을 유지한다.

## Goals

- YouTube 채널, Threads, X, LinkedIn 대상 계정을 GUI에서 등록, 수정, 비활성화할 수 있다.
- 플랫폼 자격 증명을 GUI에서 입력하고 OS 보안 저장소에 안전하게 보관할 수 있다.
- 현재 `data/skim.db`에 저장된 게시글을 GUI에서 검색, 필터링, 상세 조회할 수 있다.
- 기존 코드 기반 목록은 별도 import 스크립트로 GUI 설정 목록에 반입할 수 있다.
- 개인용 로컬 도구로 시작하지만 공개 저장소에 올려도 구조적으로 무리가 없어야 한다.

## Non-Goals

- 첫 버전에서 GUI가 전체 크롤링 배치 운영 콘솔 역할을 하지는 않는다.
- 첫 버전에서 다중 사용자, 클라우드 동기화, 팀 공유 기능은 다루지 않는다.
- 첫 버전에서 AI 요약 생성 UI, 큐레이션 세트 편집 UI는 포함하지 않는다.
- 첫 버전에서 Windows 패키징 완성은 목표가 아니며, 향후 확장 가능한 경계를 만드는 데 집중한다.

## Current State

현재 프로젝트는 Python 기반 CLI 파이프라인이다.

- 진입점: [cli.py](/Users/seungwonan/Dev/3-tool/skim/packages/skim-cli/src/skim_cli/cli.py)
- DB 스키마 정의: [db.py](/Users/seungwonan/Dev/3-tool/skim/packages/skim-core/src/skim_core/db.py)
- 공통 게시글 모델: [models.py](/Users/seungwonan/Dev/3-tool/skim/packages/skim-core/src/skim_core/models.py)
- YouTube 기본 목록: [feed_config.py](/Users/seungwonan/Dev/3-tool/skim/packages/skim-core/src/skim_core/feed_config.py)
- 로그인 세션 저장 경로: `data/sessions/{platform}_session.json`

기존 SQLite는 아래 테이블을 사용한다.

- `posts`
- `summaries`
- `feedback`
- `runs`

`posts`는 탐색 GUI의 핵심 데이터 소스다. 주요 컬럼은 아래와 같다.

- 식별/출처: `id`, `platform`, `source`, `external_id`
- 본문/메타: `author`, `title`, `content`, `url`, `timestamp`
- 반응 데이터: `likes`, `comments`, `reposts`, `views`
- 후처리 데이터: `summary`, `content_markdown`, `word_count`
- 확장 데이터: `extra`
- 수집 시각: `crawled_at`

## Recommended Architecture

권장 구조는 `Tauri shell + JS/TS app + Python sidecar`다.

### Tauri Shell

- 데스크톱 창, 메뉴, 파일 저장 대화상자, OS 통합을 담당한다.
- macOS를 첫 타깃으로 잡되, Windows 지원을 염두에 둔 권한 경계를 유지한다.
- OS 보안 저장소와의 연결은 Tauri 네이티브 계층에서 처리한다.

### JS/TS App

- 앱의 주된 UI를 제공한다.
- 설정 화면과 탐색 화면을 담당한다.
- SQLite 읽기용 query layer를 담당한다.
- 소스 등록, 검색, 필터링, 결과 상세 표시를 담당한다.
- 기존 config import 명령의 실행과 결과 표시를 담당한다.

### Python Sidecar

- 기존 크롤러 자산을 유지한다.
- 로그인 세션 추출, 플랫폼별 수집, enrichment, DB 쓰기를 담당한다.
- GUI는 sidecar를 직접 조작하지 않고 제한된 명령 인터페이스만 호출한다.

### SQLite

- 기존 `data/skim.db`를 그대로 유지한다.
- 기존 결과 테이블은 계속 Python이 기록한다.
- GUI 설정용 테이블만 추가한다.

## Why Not Full JS Rewrite

이번 단계에서 로직 전체를 JS로 재작성하지 않는다.

- 현재 가치의 중심은 이미 동작하는 Python 크롤러 자산이다.
- Threads, X, LinkedIn 로직은 재작성보다 재검증 비용이 더 크다.
- GUI의 핵심 요구는 설정과 탐색이며, 이는 JS/TS 레이어에서 충분히 해결할 수 있다.
- 장기적으로 crawler별 점진적 이전은 가능하지만 첫 GUI 릴리스의 범위로는 과하다.

## App Modules

앱은 아래 모듈 경계를 가진다.

### `desktop-shell`

- Tauri 애플리케이션 부트스트랩
- 메뉴, 창, 플랫폼별 권한 설정

### `settings-store`

- GUI 설정 테이블 CRUD
- 소스 등록, 수정, 비활성화, 우선순위 관리

### `credential-vault`

- OS 보안 저장소에 비밀번호 저장
- 저장된 credential reference 조회
- 세션 상태와 자격 증명 메타데이터 연결

### `query-layer`

- `posts` 조회
- 플랫폼/작성자/기간/키워드/지표 필터 조합
- 페이지네이션, 정렬, 상세 조회

### `python-bridge`

- sidecar 실행
- 로그인 세션 추출 명령 호출
- 세션 상태 검증
- 기존 config import 스크립트 호출

## Data Model Changes

기존 결과 테이블과 별개로 설정 전용 테이블을 추가한다.

### `tracked_sources`

사용자가 GUI에서 관리하는 수집 대상 목록이다.

- `id INTEGER PRIMARY KEY`
- `platform TEXT NOT NULL`
- `source_type TEXT NOT NULL`
- `display_name TEXT NOT NULL`
- `canonical_id TEXT NOT NULL`
- `handle_or_url TEXT`
- `is_enabled INTEGER NOT NULL DEFAULT 1`
- `focus_level INTEGER NOT NULL DEFAULT 0`
- `notes TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`
- `UNIQUE(platform, canonical_id)`

의미:

- YouTube는 최종적으로 `channel_id`를 `canonical_id`에 저장한다.
- Threads, X, LinkedIn은 플랫폼별 고유 식별자 또는 정규화된 프로필 식별값을 저장한다.
- `focus_level`은 “집중해서 보고 싶은 대상” 우선순위를 표현한다.

### `platform_credentials`

플랫폼별 로그인 계정과 보안 저장소 참조 정보를 보관한다.

- `id INTEGER PRIMARY KEY`
- `platform TEXT NOT NULL`
- `account_label TEXT NOT NULL`
- `login_identifier TEXT NOT NULL`
- `secret_service TEXT NOT NULL`
- `secret_account TEXT NOT NULL`
- `session_path TEXT`
- `session_status TEXT NOT NULL DEFAULT 'missing'`
- `last_verified_at TEXT`
- `created_at TEXT NOT NULL`
- `updated_at TEXT NOT NULL`

의미:

- SQLite에는 비밀번호를 저장하지 않는다.
- `secret_service`와 `secret_account`는 OS 보안 저장소에서 실제 비밀값을 찾기 위한 키다.
- `session_path`는 기존 구조를 따라 `data/sessions/{platform}_session.json`를 가리킨다.
- `session_status`는 `missing`, `healthy`, `expired`, `invalid` 중 하나를 사용한다.

### `app_settings`

앱 전역 설정을 보관한다.

- `key TEXT PRIMARY KEY`
- `value TEXT NOT NULL`

초기 후보 키:

- `db_path`
- `export_dir`
- `default_date_range`

## Credential Strategy

자격 증명은 아래 원칙으로 관리한다.

- 사용자가 GUI에서 아이디와 비밀번호를 입력한다.
- 비밀번호는 즉시 OS 보안 저장소에 저장한다.
- SQLite에는 비밀번호를 절대 저장하지 않는다.
- GUI에는 `account_label`, `login_identifier`, `session_status`, `last_verified_at`만 노출한다.
- 세션 쿠키는 기존과 동일하게 `data/sessions/*.json`에 저장한다.

첫 릴리스 기준:

- macOS: Keychain 지원
- Windows: 이후 Credential Manager provider를 추가할 수 있도록 인터페이스를 분리

## Source Registration Rules

### YouTube

- 입력은 `채널 URL`, `handle`, `channel_id`를 모두 허용한다.
- 저장 전 정규화 과정을 거쳐 `channel_id`를 `canonical_id`로 고정한다.
- 중복 기준은 `platform + canonical_id`다.

### Threads, X, LinkedIn

- 입력은 우선 `프로필 URL` 또는 `핸들/식별자`를 허용한다.
- 저장 시 정규화된 식별값을 만든다.
- 가능한 경우 원본 입력도 `handle_or_url`에 남긴다.

## Import Strategy

앱은 기본적으로 빈 상태에서 시작한다. 기존 코드 기반 목록은 자동으로 가져오지 않는다.

대신 별도 import 스크립트를 제공한다.

- 입력 소스: [feed_config.py](/Users/seungwonan/Dev/3-tool/skim/feed_config.py)
- 1차 범위: `YOUTUBE_CHANNELS`
- 동작:
  - 미리보기
  - 신규 항목 수 표시
  - 중복 항목 스킵
  - 가져오기 결과 리포트

중복 기준:

- `tracked_sources.platform`
- `tracked_sources.canonical_id`

## Screen Structure

첫 버전 화면은 세 개로 제한한다.

### `Sources`

수집 대상 관리 화면이다.

주요 기능:

- YouTube 채널 추가
- Threads/X/LinkedIn 대상 추가
- 우선순위 설정
- 활성/비활성 토글
- 기존 config import 실행

주요 컬럼:

- 플랫폼
- 표시 이름
- 식별자
- 우선순위
- 활성 여부

### `Credentials`

플랫폼 로그인 계정 관리 화면이다.

주요 기능:

- 계정 추가
- 비밀번호 저장 또는 갱신
- 세션 로그인 시작
- 세션 상태 확인

주요 컬럼:

- 플랫폼
- 계정 레이블
- 로그인 식별자
- 세션 상태
- 마지막 확인 시각

### `Explorer`

저장된 게시글 탐색 화면이다.

필터:

- 플랫폼
- 작성자 또는 소스
- 기간
- 키워드
- 반응값 최소치
- `content_markdown` 유무

결과 기능:

- 리스트 보기
- 상세 패널 보기
- 원문 링크 열기

## User Flows

### First Run

1. 앱 실행
2. 빈 상태 확인
3. `Sources`에서 직접 등록하거나 import 실행
4. `Credentials`에서 로그인 계정 저장
5. 필요 시 세션 로그인 실행
6. `Explorer`에서 기존 데이터 탐색

### Import Existing YouTube List

1. `Sources`에서 import 버튼 선택
2. `feed_config.py` 기반 미리보기 표시
3. 신규/중복 항목 수 표시
4. 사용자가 확인 후 반입

### Save Credential

1. 사용자가 플랫폼과 로그인 식별자를 입력
2. 비밀번호 입력
3. 앱이 OS 보안 저장소에 저장
4. SQLite에 credential reference와 메타데이터만 저장

### Explore

1. 사용자가 필터와 검색 조건을 조합
2. 결과 목록 확인
3. 필요한 항목의 상세 패널 확인
4. 원문 링크 열기

## App to Backend Contract

JS/TS와 Python 사이 계약은 좁게 유지한다.

초기 명령 인터페이스:

- `start_login(platform, credential_ref)`
- `verify_session(platform)`
- `import_feed_config(path)`

향후 확장 가능:

- `run_crawl(platforms, filters, options)`

원칙:

- JS/TS는 크롤러 내부 구조를 모른다.
- Python은 UI 상태를 모른다.
- 둘 사이에는 작은 명령 인터페이스만 존재한다.

## Error Handling

### Credential Errors

- 보안 저장소 저장 실패
- 보안 저장소 접근 거부
- 저장된 비밀값 조회 실패

대응:

- 사용자가 다시 저장할 수 있는 액션 메시지를 제공한다.

### Session Errors

- 세션 파일 없음
- 세션 만료
- 세션 파일 손상

대응:

- `missing`, `expired`, `invalid`, `healthy` 상태를 명확히 보여준다.

### Database Errors

- DB 파일 없음
- 스키마 누락
- 파일 잠금 또는 읽기 실패

대응:

- 읽기 실패 원인과 복구 방향을 명확히 안내한다.

### Import Errors

- config 파싱 실패
- 일부 항목 정규화 실패
- 중복 충돌

대응:

- 성공 수, 스킵 수, 실패 수를 분리해서 보고한다.

### Export Errors

- 쓰기 권한 부족
- 경로 문제
- 직렬화 실패

대응:

- 다른 저장 경로를 선택하도록 안내한다.

## Testing Strategy

### JS/TS Unit Tests

- source normalization
- 필터 조합 로직
- 검색 쿼리 빌드

### Integration Tests

- 설정 테이블 CRUD
- 기존 `posts` 조회
- Python sidecar 호출 계약
- import 스크립트 결과 검증

### E2E Desktop Tests

- 소스 등록
- 자격 증명 저장
- 세션 상태 표시
- Explorer 검색 및 필터링

## MVP Acceptance Criteria

- YouTube, Threads, X, LinkedIn 수집 대상을 GUI에서 등록할 수 있다.
- 자격 증명을 GUI에서 입력하고 OS 보안 저장소에 저장할 수 있다.
- 세션 상태를 GUI에서 확인할 수 있다.
- 기존 `posts` 데이터를 플랫폼, 작성자, 기간, 키워드 기준으로 탐색할 수 있다.
- `feed_config.py` 기반 import로 기존 YouTube 목록을 가져올 수 있다.

## Future Extensions

- Windows Credential Manager provider 추가
- GUI에서 크롤링 실행과 최근 실행 상태 표시
- 큐레이션 세트 저장
- AI 요약 활용 UI
- 팀 공유 또는 클라우드 동기화
