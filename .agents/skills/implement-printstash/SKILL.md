---
name: implement-printstash
description: Implement or resume an approved PrintStash change, GitHub issue, or agreed spec through decisions, design, tests, review, and PR delivery.
---

# Implement PrintStash

This is the local route for [implement](../implement/SKILL.md).
Use [ask-matt's phase boundaries](../ask-matt/PHASE-BOUNDARIES.md) for
handoffs between design, ticketing, and implementation. Keep accepted scope
in the issue or agreed spec, substantial execution notes under `reports/`,
and evidence in the delivery PR. A task with an adequate agreed spec can
proceed without creating an issue solely to mirror that spec.

## 1. Recover the complete contract

Read the selected issue or request, comments, acceptance criteria, decisions,
and blocking relations. Read the owning guide, terms, applicable decision
records, normal entry points, and existing test seams. Verify which reported
conditions still hold in today's code.

On resume, read the existing branch, diff, PR, and outstanding review or CI.
Identify what's already delivered and what remains. Preserve decisions that
still fit and retain the current task branch. If another implementer is
active or a genuine prerequisite is absent, establish its effect before
taking over dependent work. Follow the
[GitHub tracker guide](../../../docs/agents/issue-tracker.md) for authorized
coordination. Keep a partially delivered issue open.

## 2. Settle choices and plan the work

Use [grill-printstash](../grill-printstash/SKILL.md) for genuinely unresolved
product, architectural, persistence, compatibility, or rollout choices.
Routine implementation choices belong to the implementer. When a material
choice remains, use the [design brief](references/design-brief.md) to show
the existing contract, recommended shape, trade-off, and observable proof.
Keep dependent work pending until the user decides. Reuse valid decisions
from the issue, session, and maintained docs.

Plan by complete steps through the owning seams and every affected consumer.
Name old paths to retire, compatibility to preserve, the stage at which
facts become durable, and the test seams for each acceptance criterion.
Use [create-tests](../create-tests/SKILL.md) to draft the matrix before tests
and implementation. For a large task, record the plan with references to
its sources under `reports/`. Explicitly requested coordination follows
[coordinate-printstash](../coordinate-printstash/SKILL.md). The plan must
cover the entire selected outcome before the first dependent edit.

## 3. Build and verify in coherent steps

Follow [implementation principles](../implementation-principles/SKILL.md)
and the engineering routes in [AGENTS.md](../../../AGENTS.md#engineering-guidance).
Prove data-integrity and security regressions in red first. For a new
feature, add the headline end-to-end proof along with the applicable
lower-tier tests. Run the affected checks as each step becomes testable.
Keep design and user guides aligned with the behavior as it changes.

Fix a contained problem in passing when it directly serves the task and
preserves its scope. Track independent problems as follow-up suggestions
with evidence. A problem that changes the plan itself returns to the grill.
Keep every accepted requirement in scope until the user agrees to a change.

Zoom out across the accumulated diff and the full affected owners before
review. Use the [code organization assessment](../implementation-principles/SKILL.md#code-organization)
to check ownership, declarations, dependencies, retired paths, and test
seams. Compare maintainer burden at the existing and proposed seams.
Record a brief disposition in the existing work record.

## 4. Review, settle, and verify

Apply [review-printstash](../review-printstash/SKILL.md) in fix mode to the
whole task diff. Fix confirmed in-scope findings, verify corrections, and
check the affected contract again. Bring material decisions back to the user.
Use [settle-printstash](../settle-printstash/SKILL.md) for durable discoveries
and architecture rationale. Compare final docs and changelog against the
result that will actually ship.

Close the coverage matrix. Attribute inherited, task-owned, and
environmental failures using the evidence from [run-tests](../run-tests/SKILL.md).
Report partial validation and follow-ups explicitly.

## 5. Deliver the selected change

Use [deliver-printstash](../deliver-printstash/SKILL.md) for the commit,
push, PR, and final checks. Keep the single PR reviewable and link its issue
using the correct closing or reference keyword. Do not use internal batches
as delivery boundaries. The workflow ends at the authorized boundary with
current CI, acceptance evidence, and any blocker stated. Start a separate
issue only when requested.
