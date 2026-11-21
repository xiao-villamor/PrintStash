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
