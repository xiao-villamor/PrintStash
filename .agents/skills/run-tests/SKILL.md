---
name: run-tests
description: >
  Run or troubleshoot PrintStash verification, select local lanes and CI shards, or assess a coverage-floor result.
---

# Run tests

Follow [running tests](references/running-tests.md) to select affected lanes,
handle failures, and identify the final CI gates. If verification reveals an
uncovered contract or a test needs an edit, use
[create-tests](../create-tests/SKILL.md).

Run the smallest useful local checks first. Report the exact commands and
outcomes, including environment failures and inherited failures. Match hosted
results to the final commit. Use Deep CI for the backend handoff gate and
release verification as prescribed by the reference.

## Select a lane by the claim

Identify the affected contract, target paths, and resources before running
checks. A leaf change calls for its focused tests. Shared schema, storage,
transport, or worker changes also reach the caller tests and applicable
compatibility lanes. Guidance-only edits use artifact checks and affected
repository guards, while the required PR check still runs on the task head.

Start with the smallest run that can falsify the claim. Verify important
differences in the environments that expose them, such as PostgreSQL,
optional storage, an external provider contract, or the real web stack.
Record exactly what was selected. If selection unexpectedly finds nothing,
fix the path or marker rather than accepting an empty run.

## Diagnose a failure

Capture the failing test, lane, commit, resource state, and full assertion
before changing anything. Re-run the focused path with the same inputs.
Compare base behavior when deciding whether the failure predates this task.
If the test only fails in the lane, inspect test order, shared state, worker
timing, and external resource readiness in that lane. Preserve the assertion
and environment constraints. Use [create-tests](../create-tests/SKILL.md)
for required coverage changes.

Pair each required check with the final head and its authoritative result.
Report the backend Deep CI partitions separately from the overall workflow
conclusion when other jobs differ.
