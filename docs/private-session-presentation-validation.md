# Private presentation retirement

M1/M3 requirement: cached JSON isolation is insufficient when mounted components
retain old private snapshots or drafts. An authority-scope change keeps valid
identity/cookie metadata but retires private work and presentation.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | discards private component state on a new session | Edge | same account signs in again with retained draft | new scope has no prior draft | Frontend unit | ✅ `src/lib/__tests__/auth-provider.test.tsx::AuthProvider::discards private component state on a new session` |
| 2 | retires private state without logging out | Happy | changed authorization scope | stored identity retained; old request scope rejected | Frontend unit | ✅ `src/lib/__tests__/auth-store.test.ts::retirePrivateSessionScope::retires private state without logging out` |

Red evidence: retained child draft survived a session change (1 failed).
After keyed private composition and scope retirement: 89 tests / 6 files passed
(auth provider/store, transport scope/request, browse client/query). The authority
revision consumer is still pending; this proves the retirement seam, not the full
permission-revocation flow.
