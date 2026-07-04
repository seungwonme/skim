# Phase 4 — `sync-plugin.sh` + 배포 타겟

> Current implementation note: the active skill is `.claude/skills/skim/SKILL.md`.
> Out-of-tree execution should use `uv tool run --from ...`, not the older
> `uv run --from ...` examples below.

## 목표

로컬 개발 → 여러 호스트(Claude Code 플러그인 캐시, `~/.agents`, `~/.codex`) 동기화 + git-install 기반 설치를 자동화한다. PyPI 배포는 v1 이후로 미룸.

## 산출물

- `scripts/sync-plugin.sh` — 로컬 심볼릭 링크/복사
- `scripts/test-plugin-install.sh` — git install 통합 검증
- `README.md` — 설치 섹션
- `.github/workflows/plugin-release.yml` (선택) — 태그 시 검증

## 배포 방식

### 사용자 설치 (git install)

```bash
# Claude Code에 플러그인 등록 (5차 리뷰 P1-7: local/ 경로는 conflict 대상이라 제거됨)
claude plugin install github.com/seungwonme/skim
```

Claude Code 가 내부적으로 `~/.claude/plugins/cache/` 에 clone. 수동 설치는 `sync-plugin.sh` 경유 (로컬 개발자 한정).

### 실제 CLI는 uv로 on-demand 실행 (2차 리뷰 #2-7 대응)

SKILL.md가 호출하는 라인 — **태그 고정 필수**:

```bash
uv run --from "git+https://github.com/seungwonme/skim@v${SKIM_PLUGIN_VERSION}" skim research "$TOPIC" --emit json
```

- `@main` mutable ref 금지. plugin.json/SKILL.md 의 `version` 과 동일한 git tag 사용
- 첫 실행 시 uv가 git clone + build 수행. 이후는 캐시 사용
- 로컬 개발: `SKIM_LOCAL_PATH=/path/to/skim` export → `uv run --from <path>` 로 switch
- Offline 설치: `uv tool install git+...@v${VERSION}` 후 `skim` 직접 호출
- PyPI 없이도 완전 동작

## sync-plugin.sh 설계 (codex review #12 대응)

**Claude Code 플러그인 캐시만 동기화**. `~/.agents`, `~/.codex` 는 스킬 시스템이 다르고 skim 과 무관 → 제거.

```bash
#!/usr/bin/env bash
set -euo pipefail

SRC="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$HOME/.claude/plugins/cache/skim"
LOCAL_CONFLICT="$HOME/.claude/plugins/local/skim"
SKILLS_CONFLICT="$HOME/.claude/skills/skim"

# 설치 경로 충돌 사전 검증 (2차 리뷰 #2-5, 7차 리뷰 P2-11: Phase 3 preflight 와 exit 5 로 통일)
for conflict in "$LOCAL_CONFLICT" "$SKILLS_CONFLICT"; do
  if [ -d "$conflict" ]; then
    echo "ERROR: conflicting install at $conflict" >&2
    echo "  Remove it before running sync-plugin.sh (slash menu would show duplicates)." >&2
    exit 5
  fi
done

echo "--- Syncing to $TARGET ---"
mkdir -p "$TARGET"
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '.pytest_cache' \
  "$SRC/.claude-plugin/" "$TARGET/"

echo "Sync complete."
```

**주의사항**:
- `~/.claude/plugins/cache/skim` 단일 타겟. `plugins/local/skim`, `skills/skim` 공존 시 스크립트가 exit 5 로 차단 (2차 리뷰 #2-5, 7차 리뷰 P2-11)
- rsync `--delete` 는 `plugins/cache/skim` 에만 한정 — 사용자가 건드리지 않는 디렉터리 전제
- Hermes/OpenClaw/Codex skills 는 별개 시스템. v2 이후 기획

## 버전 관리 전략 (codex review #13 대응)

**플러그인 릴리즈와 Swift desktop 릴리즈를 분리**한다. desktop 앱은 research 기능을 소비하지 않으므로 동시 bump 강제는 불필요한 release train 결합.

| 소스 | 버전 위치 | 플러그인 릴리즈 시 bump? |
|---|---|---|
| Python 패키지 (skim-cli) | `packages/skim-cli/pyproject.toml` | **예** |
| Python 패키지 (skim-core) | `packages/skim-core/pyproject.toml` | **예** |
| 플러그인 | `.claude-plugin/plugin.json` `version` | **예** |
| SKILL.md | frontmatter `version` | **예** |
| Swift desktop 앱 | `apps/desktop/Package.swift`, `scripts/build-app.sh` generated `Info.plist` | 아니오 — 독립 release train |

**규칙**: 플러그인/CLI 4곳은 동일 semver 로 동기 bump. desktop 은 해당 앱이 새 research 기능을 소비하기 시작할 때만 함께 bump.

배포 전 체크 스크립트:

```bash
# scripts/check-versions.sh
# 플러그인 릴리즈 대상 4개 파일의 버전이 일치하는지 + git tag 와 동일한지 검증 (2차 리뷰 #2-7).
set -euo pipefail

extract() { grep -oE '[0-9]+\.[0-9]+\.[0-9]+' "$1" | head -n 1; }

CLI_VER=$(extract packages/skim-cli/pyproject.toml)
CORE_VER=$(extract packages/skim-core/pyproject.toml)
PLUGIN_VER=$(extract .claude-plugin/plugin.json)
SKILL_VER=$(extract .claude-plugin/skills/skim/SKILL.md)

if [ "$CLI_VER" != "$CORE_VER" ] || [ "$CORE_VER" != "$PLUGIN_VER" ] || [ "$PLUGIN_VER" != "$SKILL_VER" ]; then
  echo "FAIL: versions differ — cli=$CLI_VER core=$CORE_VER plugin=$PLUGIN_VER skill=$SKILL_VER" >&2
  exit 1
fi

# 3차 리뷰 P1-6: SKILL.md 의 version 이 Step 2 bash 에서 awk 로 추출되므로
# frontmatter 포맷이 바뀌면 런타임 파싱 실패. 파싱 방식 자체를 회귀 보호.
PARSED=$(awk -F'"' '/^version:/ {print $2; exit}' .claude-plugin/skills/skim/SKILL.md)
if [ "$PARSED" != "$SKILL_VER" ]; then
  echo "FAIL: SKILL.md version parse mismatch — parsed=$PARSED expected=$SKILL_VER" >&2
  echo "       Check frontmatter quoting: should be \`version: \"X.Y.Z\"\`" >&2
  exit 1
fi

# 릴리즈 태그 릴리즈 검증 (CI 에서만)
if [ -n "${GITHUB_REF:-}" ] && [[ "$GITHUB_REF" == refs/tags/v* ]]; then
  TAG_VER="${GITHUB_REF#refs/tags/v}"
  if [ "$TAG_VER" != "$CLI_VER" ]; then
    echo "FAIL: git tag v$TAG_VER differs from version $CLI_VER" >&2
    exit 1
  fi
fi

echo "OK: all versions match ($CLI_VER)"
```

## git install 검증 스크립트

```bash
#!/usr/bin/env bash
# scripts/test-plugin-install.sh
set -euo pipefail

# 임시 디렉터리에서 git install 재현
TMPDIR=$(mktemp -d)
trap "rm -rf $TMPDIR" EXIT

export SKIM_WORKSPACE_ROOT="$TMPDIR/skim"
mkdir -p "$SKIM_WORKSPACE_ROOT"

uv run --from "git+file://$(pwd)" skim --help
uv run --from "git+file://$(pwd)" skim research "test" --refresh never --emit json
```

## 릴리즈 절차 (수동, v0)

1. `Phase 1~3` 전 테스트 통과 확인: `just test` + 필요한 경우 `just e2e`
2. 버전 bump (4개 파일: skim-cli/skim-core pyproject, plugin.json, SKILL.md). desktop 은 제외
3. `CHANGELOG.md` 업데이트
4. `bash scripts/sync-plugin.sh` 로 로컬 동기화 후 Claude Code 재시작하여 `/skim test` 수동 검증
5. `git tag v0.3.0` + push (플러그인 전용 태그. desktop 릴리즈는 별도 태그 체계)
6. GitHub Release 작성
7. README 설치 안내 업데이트

## CI 후보 (v1)

```yaml
# .github/workflows/plugin-release.yml
on:
  push:
    tags: ['v*']
jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v3
      - run: uv sync
      - run: uv run pytest tests -v
      - run: bash scripts/check-versions.sh
      - run: bash scripts/test-plugin-install.sh
```

## TDD / 수동 체크리스트

- [ ] (수동) `bash scripts/sync-plugin.sh` 실행 후 `~/.claude/plugins/cache/skim/` 동기화 확인
- [ ] (수동) Claude Code 재시작 → `/skim` 슬래시 메뉴 노출
- [ ] (수동) `/skim nvidia` 실행 → JSON 받아 리포트 생성 흐름
- [ ] (수동) 세션 없을 때 skip+warning 실제 동작
- [ ] (수동) `SKIM_WORKSPACE_ROOT` 미지정 상태에서 `uv run --from git+...` 호출 시 DB 위치 확인 → `~/.skim/data/skim.db` 이 아니면 플러그인 버그
- [ ] (수동) `SKIM_LOCAL_PATH` export 후 `/skim test` → 로컬 checkout 이 실행되는지 (2차 리뷰 #2-4)
- [ ] `test_sync_script_targets_only_claude_cache` — `~/.agents`, `~/.codex` 로 쓰기 시도 안 하는지
- [ ] `test_sync_script_blocks_on_conflicting_install_paths` — `plugins/local/skim` 또는 `skills/skim` 존재 시 exit 5 (2차 리뷰 #2-5, 7차 리뷰 P2-11)
- [ ] `test_version_strings_match_across_4_files` — pyproject × 2, plugin.json, SKILL.md version 동일
- [ ] `test_version_matches_git_tag_in_release_ci` — 태그 릴리즈 시 버전 일치 강제 (2차 리뷰 #2-7)
- [ ] `test_skill_uses_version_tag_not_main` — SKILL.md 에 `@main` 문자열 부재

## 의존성

- Phase 1, 2, 3 모두 완료
- GitHub repo가 public 이거나 SSH 인증 셋업 완료

## TODO

- PyPI 배포 전환 (v1): `skim research ...` 직접 실행 (`uv tool install skim` 후 PATH 경유)
- Homebrew tap (v2)
- Hermes / OpenClaw 타겟 추가 (last30days 참고)
- autoupdate: `skim self-update` 커맨드
- 플러그인 마켓플레이스(`anthropics/claude-code-plugins` 같은 곳) 제출
