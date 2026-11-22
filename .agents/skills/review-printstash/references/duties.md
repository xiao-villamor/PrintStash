# Review duties

Use the same pinned diff and relevant surrounding code for each duty. Treat
tooling output as evidence. Verify a supported example or test result before
claiming a failure.

| Standards duty | Evidence to inspect |
| --- | --- |
| Backend | Capability ownership, public dependencies, scope and authorization, real atomicity, migration upgrades, local and optional deployment behavior, scale, durable work recovery. Follow [backend engineering](../../backend-engineering/SKILL.md). |
| Frontend | Route and state ownership, keyboard and focus, tokens and motion, localization, preview parity, and states the affected user can reach. Follow [frontend engineering](../../frontend-engineering/SKILL.md). |
| Integration | Inspect the producer and every affected caller. Check query scope, version and authority, write receipts, cache invalidation, and handling of ambiguous or interrupted work. Follow [frontend-backend integration](../../frontend-backend-integration/SKILL.md). |
| Tests | Match each acceptance criterion to a test that asserts it. Inspect test tier, failure injection, isolation, and fixture parity. Follow [create-tests](../../create-tests/SKILL.md). |
| Agent guidance | Follow [writing-for-agents](../../writing-for-agents/SKILL.md). Verify scope, routes, file and command references, and managed-skill provenance. |

For the **Spec** pass, compare the full accepted contract to behavior and
test evidence. Check omitted or partial requirements and changes beyond scope.
Compare user guides, release notes, glossary, and contract snapshots to the
delivered result. If an agreed decision settles a doc mismatch, cite it and
update the owner in fix mode. If intent is unclear, raise the decision with
the affected surface and options.

Inspect the entire affected owner when reviewing structure. Use
[implementation principles](../../implementation-principles/SKILL.md) to
evaluate ownership and missing or superfluous boundaries. Record issues
outside the task as follow-ups without treating them as blockers for the
requested diff.
