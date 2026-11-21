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

Done when the user has settled every material question required for the next
phase and each answer has a durable destination or an explicit place in the
agreed plan.
