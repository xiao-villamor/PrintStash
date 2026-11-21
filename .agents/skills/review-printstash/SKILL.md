---
name: review-printstash
description: Review a PrintStash branch, PR, or diff for repository standards and accepted scope, or verify and fix findings during implementation.
---

# Review PrintStash

Use the Standards and Spec axes, spec discovery, and smell heuristics from
[code-review](../code-review/SKILL.md). Apply both axes in the current
session. When the user explicitly requests subagents in this request, use
read-only lanes through [coordinate-printstash](../coordinate-printstash/SKILL.md).
Standards and Spec each get their own result even when one session runs them.

Choose **report mode** for standalone review and **fix mode** for
implementation or a request to review and fix. Report mode ends with the
verified findings. Fix mode continues through repairs and re-review.

## 1. Pin the diff and intent

Resolve the requested fixed point and reviewed head. For a PR use its
declared base and merge base unless the user specifies another comparison.
Include uncommitted changes when those are in scope. Record the exact
comparison, commit list, and relevant working-tree state. A moving branch
needs a new pinned comparison before calling it reviewed.

Gather the originating request or issue, comments and decisions, related
maintained guides, relevant rationale, and applicable scope instructions.
If no spec is available, report that limit and still check documentation
against the result. An empty diff should be reported as such.

## 2. Choose duties by behavior

Apply [review duties](references/duties.md) to each affected area and to
the accepted spec. An extraction or ownership change needs review of the
whole affected owner even if its outward behavior is meant to stay the
same. Identify who checks each changed cross-area boundary. Shared
context can stay in one pass for a small change. Split the analysis when
one pass cannot cover a large or complex contract in full.

## 3. Verify the findings

For every candidate, read the actual owner and relevant callers at the
reviewed head. Cite the violated rule or acceptance criterion and provide
the concrete path or observable consequence. Verify that it was introduced
or exposed by this change. Use a focused test, documented contract, or
precise call trace to check the claimed behavior. Discard unsupported
claims and record material coverage gaps.

Classify each confirmed item:

- **Settled fix:** the existing contract supplies the correction. Apply it
  in fix mode within the task's scope.
- **Judgement:** an unresolved design or product trade-off. Make routine
  naming, test, and local placement decisions within the existing rules.
  Route changes to accepted product behavior, public compatibility, or
  material architecture through [grill-printstash](../grill-printstash/SKILL.md).
- **Separate follow-up:** a verified issue outside the diff or agreed scope.
  State its impact and where it belongs. Search for an existing owner before
  recommending another ticket. Use [to-printstash-ticket](../to-printstash-ticket/SKILL.md)
  for authorized filing or updates.

An outdated guide can be a settled fix when a recorded decision establishes
the shipped intent. When code and docs disagree and the intent is uncertain,
surface it as a decision. A smell is evidence to inspect, with its
consequence and rationale checked before treating it as a defect.

## 4. Fix and re-review

In fix mode, combine overlapping settled fixes at the owning seam.
Run the affected checks and show how the assertions catch the finding.
Review the correction and the resulting full diff for regression and
unintended scope. Repeat for new confirmed findings. Reuse verification
that still applies. Settle only durable lessons that change future work.

## 5. Report

Keep Standards and Spec separate. For each finding show severity,
file and line, expected contract, actual consequence, and the proof needed
or obtained. Show user decisions with one recommendation each. List
follow-ups separately from defects in scope. Report reviewed head, areas
covered, unresolved evidence, and tests actually run. Keep longer ledgers
under `reports/`.
