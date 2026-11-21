---
name: coordinate-printstash
description: Coordinate PrintStash implementation or review when the user explicitly requests subagents. Assign ownership, integrate handbacks, and verify the final result.
---

# Coordinate PrintStash

Read [AGENTS.md](../../../AGENTS.md#agent-coordination). This route applies
when the current request explicitly authorizes subagents. The coordinator
keeps the user dialogue, plan, decision records, publication, and delivery.
This skill changes staffing for [implement-printstash](../implement-printstash/SKILL.md)
and [review-printstash](../review-printstash/SKILL.md). Their scope and
completion evidence still apply.

## Establish availability and ownership

Check the harness's supported delegation tools and any user-chosen model,
tier, count, or access restrictions. Surface a requested capability that
is unavailable before staffing dependent work. Honor the authorized branch,
workspace, and execution mode.

Pick the fewest workers that can deliver the approved steps. Use one worker
for a long, tightly coupled implementation. Split work when each part has
a written interface and disjoint ownership. Assign exactly one writer to
shared contracts, schema history, registries, generated files, and docs.
Sequence workers whose changes must compile or migrate together. Agree on
wire shape, canonical names, and verification before the first edit.

Keep a private roster under `reports/` with plan steps, worker/lane
identifiers, ownership, status, and returned SHA or diff. Update it when a
handoff changes any of those facts.

## Brief workers on their actual scope

Each brief includes the objective and accepted scope, exact owned paths,
available dependencies and contracts, the applicable skill routes, expected
focused test matrix and commands, what decisions to escalate, and handback
format. Include all necessary context in the brief where the worker starts
fresh. A worker needs neither credentials nor the rest of the conversation.

In a shared workspace, use path-scoped edits and staging. Coordinate
commits so a worker cannot capture a sibling's staged changes. The
coordinator owns tree-wide Git operations and branch delivery. Workers
raise material decisions to the coordinator, and work outside assigned
files is a suggestion unless reassigned.

## Integrate and verify

On each handback, check the diff, actual commits, and test output against
the brief. Reconcile contracts before dependent work starts. A worker's
stated success is provisional until the coordinator checks its evidence.
Relay material decisions and their confirmed outcomes to the affected
workers. Batch related fixes for the same owner.

After all writers stop, review the coherent integrated state. Apply the
implementation-principles organization assessment to the whole change.
Run the checks that span workers, especially integration and contract
tests whose assumptions were split across them.

## Review and close

Review lanes read one pinned diff and hold named Standards or Spec duties.
Keep them read-only. Ask each to state coverage and gaps, then verify
findings against the code and accepted contract in the coordinator session.
Route fixes to their owners and re-review the result after writes stop.
Close idle work and record every final result before PR delivery.

The coordinator reports the integrated verification and retains ownership
of user decisions, documentation settlement, and the single task PR.
