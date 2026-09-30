# skim 태스크 러너 (Python: uv / Desktop: SwiftPM)

default:
    @just --list

# 데스크톱 앱 실행
dev:
    swift run --package-path apps/desktop SkimDesktop

# Python 린트 (포맷과 import 정렬이 어긋나도 실패한다)
lint:
    uv run ruff format --check packages tests scripts
    uv run ruff check packages tests scripts
    uv run flake8 packages tests scripts
    uv run pylint packages/skim-core/src/skim_core packages/skim-cli/src/skim_cli scripts

# 테스트 (Python + Swift)
test:
    uv run pytest tests -q
    swift test --package-path apps/desktop

# 데스크톱 e2e 스모크 (fixture DB + 실제 앱 부팅)
e2e:
    sh scripts/desktop-e2e.sh

# 데스크톱 앱 빌드
build:
    swift build --package-path apps/desktop

# 데스크톱 앱 번들 빌드 + 설치 (기본 /Applications)
install-desktop *args:
    scripts/build-app.sh {{args}}

# 포매터 (import 정렬 후 포맷)
format:
    uv run ruff check --fix packages tests scripts
    uv run ruff format packages tests scripts

# 크롤 (예: just crawl hackernews --days 1)
crawl *args:
    uv run skim crawl {{args}}
