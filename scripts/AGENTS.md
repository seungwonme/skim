# scripts

Import, cron, and maintenance scripts.

## Rules

- Keep scripts deterministic and runnable from the repo root unless documented otherwise.
- Prefer standard library Python or shell for small maintenance scripts.
- Do not make scripts depend on desktop build output or local runtime data.
- Scripts should fail with actionable messages and non-zero exit codes.
- Keep path handling workspace-relative where possible.
- Avoid modifying tracked source files unless the script name and help text make that intent explicit.
- Add tests or a documented smoke command for behavior-changing script edits.
