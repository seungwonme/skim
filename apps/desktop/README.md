# Skim Desktop

macOS 우선 `skim` 데스크톱 앱이다. `Tauri + React + TypeScript`로 작성되었고, 기존 Python 프로젝트의 로컬 SQLite와 세션 파일을 그대로 사용한다.

## Commands

```bash
pnpm install
pnpm build
pnpm tauri dev
```

## Current Scope

- 추적 대상 등록과 편집
- macOS Keychain 자격 증명 저장
- 세션 상태 확인과 브라우저 로그인 시작
- `data/skim.db` 탐색
