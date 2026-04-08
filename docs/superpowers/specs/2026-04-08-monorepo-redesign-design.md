# Skim Monorepo Redesign

## Summary

`skim` 저장소를 Python CLI, React/Tauri desktop, Rust backend가 공존하는 표준 모노레포로 재구성한다. 목표는 현재의 혼합 루트 구조를 `apps/*`, `packages/*`, 루트 workspace orchestration 계층으로 분리하고, 설치/개발/검증/훅/환경 변수 규칙을 루트에서 일관되게 관리하는 것이다. 기존 진입점과 경로 호환성은 유지하지 않고, 새 모노레포 진입점으로 완전히 전환한다.

## Goals

- 저장소 루트가 “실행 코드가 섞여 있는 폴더”가 아니라 workspace orchestration 계층이 되도록 재구성한다.
- `desktop/`, 루트 `src/`, 루트 `main.py` 중심 구조를 `apps/desktop`, `packages/skim-core`, `packages/skim-cli`로 분리한다.
- JavaScript/TypeScript 작업은 `pnpm workspace + turbo` 기준으로 통일한다.
- Python 작업은 `uv` 기준으로 유지하되 패키지 단위 메타데이터와 진입점을 분리한다.
- Rust/Tauri는 앱 내부에 두되 루트 Cargo workspace에서 공통 profile과 member 관리를 하게 만든다.
- Git hook, lint, test, build, typecheck 흐름을 루트 명령으로 일관되게 실행 가능하게 만든다.
- `.env.example`, ignore 규칙, editor/tool config를 모노레포 기준으로 재정리한다.
- README, AGENTS.md, 스크립트, CI가 새 구조와 모순되지 않게 맞춘다.

## Non-Goals

- 이번 작업에서 신규 제품 기능을 추가하지 않는다.
- Python crawler 로직을 TypeScript나 Rust로 재작성하지 않는다.
- Desktop GUI의 기능 범위를 확장하지 않는다.
- `data/`를 애플리케이션 사용자 디렉터리로 옮기지 않는다. 단, 향후 이전 가능성은 문서에 남긴다.
- 과도한 세분화(`packages/config-*`, `packages/rust-bridge` 등)로 현재 규모에 비해 복잡한 구조를 만들지 않는다.

## Current State

현재 저장소는 루트에 여러 런타임 자산이 섞여 있다.

- Python CLI 진입점: [main.py](/Users/seungwonan/Dev/3-tool/skim/main.py)
- Python 도메인 코드: `src/`
- Desktop 앱: `desktop/`
- Tauri/Rust backend: `desktop/src-tauri/`
- Python 패키지 메타데이터: [pyproject.toml](/Users/seungwonan/Dev/3-tool/skim/pyproject.toml)
- Git hook/lint 설정: [.pre-commit-config.yaml](/Users/seungwonan/Dev/3-tool/skim/.pre-commit-config.yaml), [.flake8](/Users/seungwonan/Dev/3-tool/skim/.flake8), [.pylintrc](/Users/seungwonan/Dev/3-tool/skim/.pylintrc)

이 구조의 문제는 아래와 같다.

- 루트가 Python 프로젝트이자 Desktop 프로젝트이자 공용 작업 디렉터리 역할을 동시에 한다.
- Python 코드가 패키지 경계 없이 루트 `src/`와 `main.py`에 결합되어 있다.
- Desktop 앱은 자체적으로는 분리되어 보이지만 모노레포 orchestration 관점의 루트 workspace와 연결되어 있지 않다.
- Git hooks는 Python 중심이며 TS/Rust 검증을 모노레포 표준 방식으로 관리하지 않는다.
- README와 개발 명령이 언어별/앱별 진입점을 통합해서 설명하지 못한다.
- `.gitignore`가 Python 템플릿 중심이라 monorepo build artifact와 cache 관리가 불완전하다.

## Recommended Architecture

권장 구조는 아래와 같다.

```text
.
├── apps/
│   └── desktop/
│       ├── package.json
│       ├── src/
│       └── src-tauri/
├── packages/
│   ├── skim-cli/
│   │   ├── pyproject.toml
│   │   └── src/skim_cli/
│   └── skim-core/
│       ├── pyproject.toml
│       └── src/skim_core/
├── tooling/
│   ├── scripts/
│   └── git-hooks/
├── docs/
├── data/
├── package.json
├── pnpm-workspace.yaml
├── turbo.json
├── Cargo.toml
├── .env.example
└── AGENTS.md
```

핵심 원칙은 아래와 같다.

- `apps/*`는 사용자-facing application 단위다.
- `packages/*`는 재사용 가능한 도메인/엔트리포인트 단위다.
- 루트는 의존성 orchestration, 공통 개발 UX, CI, Git hooks, 문서 진입점만 담당한다.
- 각 언어 런타임은 자기 패키지의 메타데이터와 명령을 갖고, 루트는 그것들을 조합한다.

## Package Boundaries

### `packages/skim-core`

Python 도메인 레이어다.

- 포함 범위:
  - crawler registry
  - crawler implementations
  - auth helpers
  - DB access
  - models
  - enrichment
  - feed utils
  - exporters
- 제외 범위:
  - CLI parsing
  - 사용자-facing Typer command wiring
  - monorepo task orchestration

이 패키지는 “CLI가 없어도 import 해서 사용할 수 있는 순수 Python 라이브러리”여야 한다.

### `packages/skim-cli`

Python CLI 엔트리포인트 레이어다.

- 포함 범위:
  - Typer app
  - command definitions
  - `skim` console script
  - CLI용 보조 스크립트
- 의존 관계:
  - `skim-cli` → `skim-core`

`main.py`는 제거하고, CLI 진입은 패키지 기반 console script로 고정한다.

### `apps/desktop`

React/Tauri app 단위다.

- 포함 범위:
  - Vite/React UI
  - Tauri configuration
  - Rust backend
  - Desktop build/test/typecheck scripts
- 책임:
  - desktop UX
  - local app integration
  - workspace root/data/session/env 해석

### `tooling/`

루트에서만 쓰는 운영 자산을 모은다.

- `tooling/scripts/`
  - cron/launchd 예시 스크립트
  - import/maintenance helper
- `tooling/git-hooks/`
  - husky hook에서 호출할 스크립트

## Workspace Strategy

### JavaScript / TypeScript

루트 `pnpm-workspace.yaml`과 `package.json`을 만든다.

- workspace members:
  - `apps/*`
  - 필요 시 향후 `packages/*` 중 JS 패키지
- 루트 devDependencies:
  - `turbo`
  - `husky`
  - `@commitlint/cli`
  - `@commitlint/config-conventional`
  - `biome`

루트 `package.json`은 monorepo entrypoint 역할을 한다.

예상 스크립트 책임:

- `dev`
- `build`
- `lint`
- `test`
- `typecheck`
- `format`
- `desktop:dev`
- `desktop:build`

### Turbo

루트 `turbo.json`은 task pipeline만 담당한다.

표준 task:

- `dev`
- `build`
- `lint`
- `test`
- `typecheck`
- `format`

원칙:

- `dev`는 cache하지 않는다.
- `build`, `lint`, `test`, `typecheck`는 cache 가능하게 둔다.
- 루트에서 개별 앱/패키지 명령을 직접 재구현하지 않고 workspace script를 재사용한다.

### Python / uv

Python은 `uv`를 유지한다. 단, 루트 단일 `pyproject.toml` 대신 package 단위 `pyproject.toml`로 전환한다.

권장 원칙:

- `packages/skim-core/pyproject.toml`
- `packages/skim-cli/pyproject.toml`
- 루트에는 Python 앱 코드 메타데이터를 두지 않는다.

루트 README와 task 문서에서는 아래 진입점을 안내한다.

- `uv sync --package skim-cli`
- `uv run --package skim-cli skim platforms`
- `uv run --package skim-cli skim crawl ...`

실제 `uv` subcommand 형태는 구현 시점의 지원 방식에 맞추되, 사용자-facing 문서는 “루트에서 `uv run skim ...` 수준으로 사용 가능”하게 정리한다.

### Cargo Workspace

루트 `Cargo.toml`을 workspace manifest로 추가한다.

- member:
  - `apps/desktop/src-tauri`
- 역할:
  - workspace member 등록
  - 공통 profile 관리

Rust 코드는 계속 앱 내부에 둔다. 현재 규모에서 별도 Rust 패키지 분리는 과하다.

## Tooling and Quality Gates

## Git Hooks

Git hooks는 `husky`를 기본으로 한다. 기존 `.pre-commit-config.yaml` 중심 운영은 단계적으로 제거하거나 보조 도구로 축소한다.

이유:

- 혼합 언어 monorepo에서 hook 진입점은 Node 기반 workspace orchestration이 더 단순하다.
- `pre-commit` 하나에 Python/TS/Rust 전체를 몰아넣으면 디버깅과 selective execution이 불편하다.
- `husky`는 commit-msg, pre-commit, pre-push를 workspace 스크립트와 직접 연결하기 쉽다.

표준 훅:

- `pre-commit`
  - 빠른 포맷/정적 검사만 수행
  - 예: `turbo run lint --affected`, `turbo run format --affected`
- `commit-msg`
  - Conventional Commits 검증
- `pre-push`
  - 상대적으로 무거운 검증 수행
  - 예: `turbo run test typecheck build`
  - Rust test도 여기서 포함

## Lint / Format

### TypeScript

사용자 선호에 따라 `eslint`, `prettier` 대신 `biome`을 사용한다.

- 루트 `biome.json` 또는 동등한 설정 파일을 둔다.
- TS/JS/JSON 계열 포맷과 기본 lint는 biome로 처리한다.
- suppress 주석은 반드시 이유를 남긴다.

### Python

기존 black/isort/flake8/pylint 조합은 유지 가능하지만, package 경계 기준으로 재배치한다.

최소 기준:

- format
- lint
- test

중요한 점은 “루트에서 일관된 task로 실행되는가”이며, 도구 자체를 이번 리디자인 범위에서 과도하게 바꾸는 것은 우선순위가 아니다.

### Rust

현재 범위에서는 최소한 아래를 지원한다.

- `cargo test`
- 필요 시 `cargo fmt --check`

## Environment Strategy

환경 변수는 루트 `.env.example` 하나를 표준 문서로 둔다.

원칙:

- 실제 비밀값은 `.env` 또는 OS Keychain에만 둔다.
- `.env.example`은 범주별 설명과 예시만 가진다.
- Python CLI와 Desktop/Tauri가 공유하는 변수 이름은 prefix와 의미를 통일한다.
- 프런트엔드 노출 변수와 백엔드 전용 변수는 분리한다.

예상 범주:

- crawler auth
- export integration
- desktop runtime overrides
- optional local development flags

Desktop/Tauri는 workspace root를 기준으로 env/data/session 경로를 안정적으로 찾도록 수정한다. 현재처럼 상대 경로 추측에 의존하는 부분은 모노레포 재배치 후 깨지기 쉽다.

## Data and Runtime Paths

`data/`는 계속 루트 개발 산출물로 유지한다.

포함 대상:

- `data/skim.db`
- `data/sessions/*`
- platform별 JSON output

원칙:

- 앱과 CLI 모두 루트 workspace 기준으로 `data/`를 참조한다.
- Desktop의 Rust backend는 새 경로 구조 기준으로 workspace root 계산 로직을 재검증한다.
- 향후 OS별 사용자 데이터 디렉터리 이전 가능성은 문서에 남기되, 이번 작업에서는 경로 계약을 고정한다.

## Migration Plan

### Phase 1: Workspace Skeleton

- 루트 `package.json` 추가
- 루트 `pnpm-workspace.yaml` 추가
- 루트 `turbo.json` 추가
- 루트 `Cargo.toml` workspace manifest 추가
- 루트 `.editorconfig`, `.env.example`, `.gitignore` 재정리

### Phase 2: Desktop App Move

- `desktop/` → `apps/desktop/`
- 내부 script/path/config 수정
- Tauri/Rust workspace member 경로 수정
- README와 root scripts에서 새 경로 사용

### Phase 3: Python Package Split

- `src/` → `packages/skim-core/src/skim_core/`
- import path 전환
- `main.py` 로직을 `packages/skim-cli/src/skim_cli/cli.py`로 이전
- `skim-cli`에서 `skim-core` 의존
- 테스트 import 경로 수정

### Phase 4: Hook and Tooling Integration

- husky 설치
- `commitlint` 설치
- 루트 scripts와 turbo tasks 연결
- 기존 `.pre-commit-config.yaml`은 제거하거나 보조 역할로 축소

### Phase 5: Documentation and CI Alignment

- README 재작성
- AGENTS.md 업데이트
- GitHub Actions를 새 루트 task 기준으로 정렬
- 예시 스크립트 경로 수정

## Verification Criteria

아래가 모두 통과해야 migration 완료로 본다.

### Root Workspace

- 루트에서 `pnpm install` 성공
- 루트에서 `pnpm lint` 성공
- 루트에서 `pnpm test` 성공
- 루트에서 `pnpm build` 성공

### Python CLI

- `skim` console script가 새 패키지 경로에서 동작
- `platforms` 명령 정상 동작
- 대표 `crawl` 명령 한 번 이상 성공
- 기존 테스트 스위트가 새 import 경로에서 통과

### Desktop

- `apps/desktop` dev server가 새 workspace 구조에서 정상 실행
- desktop build/typecheck 통과
- Tauri backend path resolution이 새 경로 기준으로 동작

### Git Hooks

- invalid commit message가 `commit-msg`에서 차단됨
- failing lint/test 상황에서 `pre-commit` 또는 `pre-push`가 차단 동작을 함

### Documentation

- README의 설치/실행 명령이 실제와 일치
- AGENTS.md의 아키텍처/명령 설명이 실제와 일치
- 예시 스크립트 경로가 새 구조와 일치

## Risks and Mitigations

### Risk: Python import 경로 붕괴

원인:

- 루트 `src`를 package 경로로 옮기면 상대 import와 test import가 깨질 수 있다.

대응:

- `skim_core` 패키지명을 명시적으로 도입한다.
- 테스트와 CLI 모두 package import만 사용하게 정리한다.

### Risk: Desktop path assumptions 붕괴

원인:

- 현재 Rust backend는 `desktop/src-tauri` 기준 상대 경로 추론을 사용한다.

대응:

- `apps/desktop` 이동 후 root resolution 로직과 테스트를 함께 수정한다.
- 필요 시 명시적 env override를 유지한다.

### Risk: Hook가 너무 무거워짐

원인:

- monorepo 전체 test/build를 commit 단계마다 돌리면 개발 속도가 급격히 떨어질 수 있다.

대응:

- `pre-commit`은 빠른 검증만 담당한다.
- 무거운 검증은 `pre-push`와 CI로 올린다.

### Risk: 과도한 구조화

원인:

- 현재 규모에서 너무 많은 패키지와 config 계층을 만들면 운영 복잡도만 늘 수 있다.

대응:

- 앱 1개, Python 패키지 2개, 루트 tooling 정도로 제한한다.
- shared config package 같은 확장은 실제 필요가 생길 때만 추가한다.

## Decisions Locked In

- `apps/desktop` + `packages/skim-core` + `packages/skim-cli` 구조로 간다.
- 기존 루트 진입점과 경로 호환성은 유지하지 않는다.
- 루트 JS workspace orchestration은 `pnpm + turbo`로 간다.
- Git hooks는 `husky + commitlint` 기반으로 재구성한다.
- Python은 `uv`를 유지하고 package 경계만 명확히 만든다.
- Rust/Tauri는 앱 내부 유지 + 루트 Cargo workspace member 방식으로 간다.
- 루트 `.env.example`, README, AGENTS.md, scripts, CI는 모두 새 구조 기준으로 정리한다.
