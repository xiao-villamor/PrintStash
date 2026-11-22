---
name: grill-printstash
description: >
  Grill a PrintStash idea, spec, or GitHub issue, or resolve material design decisions before implementation.
---

# Grill PrintStash

Compose [grill-with-docs](../grill-with-docs/SKILL.md) with its
[grilling](../grilling/SKILL.md) and [domain-modeling](../domain-modeling/SKILL.md)
references. Use their interview and vocabulary discipline with these local
sources and settlement rules.

1. **Ground the questions.** Read the issue or plan and
   [domain guidance](../../../docs/agents/domain.md), then inspect code and
   tests. If an existing decision settles a question, cite it. If the code and
   intended behavior disagree, show the discrepancy.
2. **Follow the decision tree.** Work in dependency order, using a
   [question card](references/question-card.md) for each unresolved decision.
   Ask only decisions the user owns. Keep unanswered questions pending.
3. **Record the result.** During implementation, carry approved decisions
   into the owning guide on the delivering branch. Store new domain terms in
   `CONTEXT.md`. Put lasting trade-offs and conventions in their approved
   homes through [settle-printstash](../settle-printstash/SKILL.md). Keep working
   reasoning under `reports/`.
4. **Sync the issue when authorized.** Update the GitHub issue's decisions,
   scope, acceptance criteria, and blockers only where they changed. Use the
   [tracker guide](../../../docs/agents/issue-tracker.md) and preserve unrelated
   content. Apply an existing readiness label only when the contract warrants it.

## Choose questions that affect the plan

Start with decisions that could change several later answers: the supported
workflow, which capability owns it, authority to change it, and whether
the change must preserve older data or clients. Probe a specific edge
case before accepting a vague term or invariant. For an import, for
example, decide whether an existing Artifact is a duplicate before
choosing how the UI reports the result or how a retry behaves.

Use established PrintStash terms and resolve a real naming ambiguity in
`CONTEXT.md`. Explore a runnable state or layout with
[prototype](../prototype/SKILL.md) when prose cannot settle the decision.
Bring the result back as evidence for the relevant card.

## Decision write-back

For every agreed choice record the question, the outcome, a short reason,
and the canonical home. Put the behavior and acceptance change in the
selected contract. Put architecture rationale in the applicable decision
record or maintained guide and link it. Put a new reusable workflow rule
in the owning skill. Update maintained docs on the branch that delivers
the behavior. Before a branch exists, list the planned doc changes in
the agreed spec or ticket.

For a GitHub issue, preserve the original problem and unrelated comments
or labels. Remove an outstanding question only when the choice is settled
and the revised acceptance criteria capture it. If unresolved human
actions remain, keep that readiness visible.

## Completion

Done when the user has settled every material question required for the next
phase and each answer has a durable destination or an explicit place in the
agreed plan.
