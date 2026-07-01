# desktop/src/components

Tabbed desktop panels live here.

## Rules

- Components receive data and callbacks from `App.tsx`; keep direct backend calls in `src/lib/api.ts` wrappers.
- Preserve the existing panel pattern: local form state, `report(...)` for user-visible status, and `onChanged()` after mutations.
- Avoid adding state managers or UI libraries for one panel change.
- Keep text compact enough for the existing desktop layout.
