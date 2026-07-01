# apps

Application packages live here. Currently the only app is `desktop/`.

## Rules

- Run app commands from the repo root unless a package script explicitly requires the app directory.
- Keep app code as a consumer of `packages/skim-cli` and `packages/skim-core`; do not duplicate crawler or DB logic here.
- Do not commit generated build output such as `.build/` or `dist/`.
