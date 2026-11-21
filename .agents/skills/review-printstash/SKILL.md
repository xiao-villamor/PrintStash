---
name: review-printstash
description: >
  Review a PrintStash branch, PR, or diff for repository standards and accepted scope, or verify and fix findings during implementation.
---

# Review PrintStash

Use the Standards and Spec axes from [code-review](../code-review/SKILL.md).
Run both passes in the current session by default. Use read-only review lanes
through [coordinate-printstash](../coordinate-printstash/SKILL.md) only with
explicit subagent authorization for this request.

1. **Pin the comparison.** Use the requested ref or the merge base with the
   PR's declared base. Identify the head and include working-tree changes
   when the user requested them. Read the issue, PR, agreed spec, or acceptance
   notes. If there is no spec, record that limit and still check docs.
2. **Review independently.** Follow [review duties](references/duties.md) for
   each affected layer and for the spec. Inspect the surrounding owner,
   dependents, and tests. Cite the rule or requirement and concrete evidence
   for each finding.
3. **Classify.** A confirmed defect cites a violated invariant or acceptance
   criterion and a concrete consequence. A decision identifies an unsettled
   trade-off and gives a recommendation. Record independently scoped work as
   a follow-up with its impact and suggested issue scope.
4. **Resolve within authorization.** A standalone review reports findings.
   In implementation or authorized fix mode, fix confirmed task-owned
   defects, rerun affected checks, and review the complete updated diff.
   Ask about unresolved material decisions through
   [grill-printstash](../grill-printstash/SKILL.md). File follow-up issues only
   when asked, using [to-printstash-ticket](../to-printstash-ticket/SKILL.md).

Report Standards and Spec separately. Lead findings with severity and
file/line or an observable failure, then explain the violated contract and
what would prove the fix. List decisions and follow-ups after findings. End
with the verified head and evidence, including missing verification. Store
longer review ledgers under `reports/`.
