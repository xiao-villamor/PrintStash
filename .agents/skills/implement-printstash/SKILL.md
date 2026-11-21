---
name: implement-printstash
description: >
  Implement or resume an approved PrintStash change, GitHub issue, or agreed spec through tests, review, and PR delivery.
---

# Implement PrintStash

This is the project workflow for upstream [implement](../implement/SKILL.md).
Use [ask-matt's phase boundaries](../ask-matt/PHASE-BOUNDARIES.md) when handing
off between design, ticketing, and delivery. Keep the issue as the contract,
the local work record as execution context, and the PR as verification evidence.

1. **Recover the contract.** Read the request or issue, comments, acceptance
   criteria, blockers, and relevant docs. For resumed work inspect the branch,
   its PR and review decisions, and remaining verification. Trace today's code
   from production entry points. Follow the [tracker guide](../../../docs/agents/issue-tracker.md)
   for authorized updates. Continue the selected issue until delivered or blocked.
2. **Resolve material decisions.** Use [grill-printstash](../grill-printstash/SKILL.md)
   for unresolved behavior, storage or API compatibility, ownership, and
   rollout choices that affect the contract. Plan vertical steps with affected
   consumers, test seams, and docs. Use the [design brief](references/design-brief.md)
   to present a material design decision.
   Use an existing settled plan when it still fits. Keep substantial plans and
   notes private under `reports/`.
3. **Implement and test.** Follow [implementation principles](../implementation-principles/SKILL.md)
   and the engineering routes in [AGENTS.md](../../../AGENTS.md#engineering-guidance).
   Use [create-tests](../create-tests/SKILL.md) before writing or editing tests.
   Complete each acceptance criterion across its owning layers and consumers.
   Reopen the grill only if new evidence invalidates an agreed decision.
   Review the accumulated structure and keep separately scoped discoveries
   available for pickup without widening the PR.
4. **Review and settle.** Run [review-printstash](../review-printstash/SKILL.md)
   in fix mode over the complete task diff. Resolve confirmed findings, verify
   acceptance and coverage evidence, and use [settle-printstash](../settle-printstash/SKILL.md)
   for durable discoveries. Update the owning guides and changelog where the
   delivered behavior changes them.
5. **Deliver.** Use [deliver-printstash](../deliver-printstash/SKILL.md) for
   the commit, push, PR, and final checks. Report accepted scope still blocked
   by a dependency or decision explicitly.

[Coordinate PrintStash](../coordinate-printstash/SKILL.md) changes staffing for
steps 3 and 4 only when the user explicitly asks for subagents in the current
request. Otherwise carry out every step in the current session.
