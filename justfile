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

# Python 테스트
test:
    uv run pytest tests -q

# 데스크톱 앱 빌드
build:
    swift build --package-path apps/desktop

# 포매터
format:
    uv run black packages tests scripts --config pyproject.toml
    uv run isort packages tests scripts --settings-path pyproject.toml

# 크롤 (예: just crawl hackernews --days 1)
crawl *args:
    uv run skim crawl {{args}}
