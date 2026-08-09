# Skim Design System

## Product model

Skim은 여러 플랫폼에서 관심 있는 소스의 글을 모아 읽는 macOS 데스크톱 앱이다. 정보 구조의 기준은 `플랫폼 > 관심 소스 > 글`이다. YouTube 채널, 블로그, 향후 LinkedIn/Threads 프로필은 모두 같은 관심 소스 계층에 속한다.

YouTube 과거 영상 불러오기와 자막 전사는 플랫폼 전체를 대표하는 메뉴가 아니라, 해당 소스와 글에서만 노출하는 보조 기능이다.

## Navigation

- 작업 공간: `피드`, `소스 관리`, `연결`
- 피드 탐색: `전체 플랫폼` 또는 플랫폼 부모를 선택한 뒤 관심 소스 자식을 선택한다.
- 소스 관리: 등록된 모든 관심 소스를 플랫폼별로 확인한다. 현재 앱에서 직접 추가할 수 있는 유형은 YouTube 채널이다.
- 연결: Threads, X, LinkedIn, Reddit 계정 메타데이터와 키체인 연결을 관리한다.

기본 화면은 macOS `NavigationSplitView`의 세 열을 유지한다.

1. 사이드바: 작업 공간과 플랫폼/관심 소스 계층
2. 목록: 글, 소스, 연결 계정
3. 상세: 리더, 소스 정보, 연결 편집기

## Visual direction

차분한 유틸리티 도구를 지향한다. 장식보다 읽기 밀도와 위치 파악을 우선한다.

- 배경은 따뜻한 중성색, 주요 동작과 선택은 절제된 초록색을 사용한다.
- 카드 중첩을 줄이고 얇은 구분선과 네이티브 목록 선택 상태를 사용한다.
- 피드 행은 소스/날짜/반응, 제목, 한 줄 미리보기 순으로 압축한다.
- 본문은 넉넉한 행간과 최대 읽기 폭을 유지한다.
- 플랫폼 고유 색은 작은 점과 배지에만 사용한다.

## Tokens

| Token | Light | Dark | Usage |
|---|---|---|---|
| Canvas | `#F4F4F0` | `#121410` | Window and reader background |
| Sidebar | `#EFEFE9` | `#161814` | Sidebar and controls |
| Surface | `#FBFBF8` | `#1A1D18` | Panels and inputs |
| Ink | `#191B18` | system label | Primary text |
| Accent | `#236F4A` | `#4CBD7B` | Selection and primary actions |
| Hairline | `#DCDDD6` | white 10% | Borders and dividers |

- Typography: SF Pro, Apple SD Gothic Neo, SF Mono
- Spacing: 4-point rhythm, primarily 8/12/16/24/32
- Radius: 7 for inputs, 9 for panels, 14 only for prominent empty states
- Motion: native macOS transitions, 120-180ms when custom motion is needed

## Interaction rules

- 플랫폼을 선택하면 해당 플랫폼의 전체 글을 보여준다.
- 관심 소스를 선택하면 `posts.source = "<platform>/<display name>"`인 글을 보여준다.
- 검색은 전체/플랫폼 피드에서는 DB 검색, 관심 소스 안에서는 로드된 소스 글 검색을 사용한다.
- 외부 링크는 기본 브라우저에서 연다. 크롤링된 Markdown의 JavaScript는 실행하지 않는다.
- 삭제는 확인 대화상자를 거친다. 비밀번호는 SQLite가 아니라 macOS 키체인에 저장한다.

## Decisions

- 2026-08-10: YouTube 전용 사이드바와 관리 모달을 제거하고 공통 플랫폼/관심 소스 계층으로 통합했다.
- 2026-08-10: 소스 관리와 연결을 독립 작업 화면으로 분리했다.
- 2026-08-10: 소스 등록 백엔드를 추측해 확장하지 않고, 현재 지원되는 YouTube 추가 기능을 명확히 표시했다.
