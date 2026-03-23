# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

### Development Setup
```bash
# Install dependencies (uv manages virtualenv automatically)
uv sync

# Install Playwright browser
uv run playwright install
```

### Linting and Formatting
```bash
# Run Black formatter
black . --config pyproject.toml

# Run isort for import sorting
isort . --settings-path pyproject.toml

# Run flake8 linter
flake8

# Run pylint
pylint src/

# Pre-commit hooks (if pre-commit is installed)
pre-commit install
pre-commit run --all-files
```

### Running the Crawler
```bash
# Basic usage for each platform
python main.py threads --count 5
python main.py linkedin --count 5
python main.py x --count 10
python main.py reddit --count 10

# Debug mode (shows browser window)
python main.py threads --debug

# Save to Google Sheets
python main.py threads --sheets
```

## Architecture Overview

### Core Structure
- **main.py**: CLI entry point using Typer framework. Handles command parsing and orchestrates crawler execution.
- **src/crawlers/**: Platform-specific crawler implementations
  - **base.py**: Abstract base class defining common crawler interface
  - **threads.py**, **linkedin.py**, **x.py**, **reddit.py**: Platform-specific implementations
- **src/models.py**: Pydantic data model for posts across all platforms
- **src/exporters/**: Data export functionality (JSON files, Google Sheets)
- **src/utils.py**: Shared utilities and helper functions

### Key Design Patterns
1. **Abstract Base Class Pattern**: All crawlers inherit from `BaseCrawler` which enforces a consistent interface through abstract methods.
2. **Async/Await Pattern**: Uses Playwright's async API for efficient browser automation.
3. **CLI Command Pattern**: Each platform has its own command with unified options.

### Crawler Flow
1. User invokes platform-specific command via CLI
2. Crawler initializes with debug mode and configuration
3. Browser launches (always visible, debug mode adds dev tools)
4. Platform-specific `_crawl_implementation` executes
5. Posts are extracted and validated using Pydantic models
6. Results saved to JSON and optionally to Google Sheets
7. Summary and preview displayed to user

### Session Management
- Each platform stores session data in `data/{platform}_session.json`
- Sessions persist login state to avoid repeated authentication
- Debug screenshots saved to `data/debug/{platform}/`

### Error Handling
- Comprehensive try-catch blocks in base crawler
- Debug mode provides detailed error information
- Platform-specific error messages guide troubleshooting

## Environment Variables
Create a `.env` file with platform credentials:
- `THREADS_USERNAME`, `THREADS_PASSWORD`
- `LINKEDIN_USERNAME`, `LINKEDIN_PASSWORD`
- `X_USERNAME`, `X_PASSWORD`
- `REDDIT_USERNAME`, `REDDIT_PASSWORD`
- `GOOGLE_WEBAPP_URL` (for Google Sheets export)

## Important Notes
- Browser window is always visible (headless=False) for transparency
- Debug mode adds developer tools and detailed logging
- Pre-commit hooks enforce code quality (Black, isort, flake8, pylint)
- All crawlers extend the abstract base class for consistency

<rules>
The following rules should be considered foundational. Make sure you're familiar with them before working on this project:
@.cursor/rules/memory-bank.mdc
@.cursor/rules/vibe-coding.mdc

Git convention defining branch naming, commit message format, and issue labeling based on GitFlow and Conventional Commits.:
@.cursor/rules/git-convention.mdc

threads 크롤러를 수정할 때 참고하세요:
@.cursor/rules/threads-crawler.mdc
</rules>
