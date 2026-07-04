# skim 태스크 러너 (Python: uv / Desktop: SwiftPM)

default:
    @just --list

# 데스크톱 앱 실행
dev:
    swift run --package-path apps/desktop SkimDesktop

# Python 린트
lint:
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

# 포매터
format:
    uv run black packages tests scripts --config pyproject.toml
    uv run isort packages tests scripts --settings-path pyproject.toml

# 크롤 (예: just crawl hackernews --days 1)
crawl *args:
    uv run skim crawl {{args}}
