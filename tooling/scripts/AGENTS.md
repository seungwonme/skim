# tooling/scripts

Import, cron, and maintenance scripts.

## Rules

- Scripts should fail with actionable messages and non-zero exit codes.
- Keep path handling workspace-relative where possible.
- Avoid modifying tracked source files unless the script name and help text make that intent explicit.
- Add tests or a documented smoke command for behavior-changing script edits.
