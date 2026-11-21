---
name: create-tests
description: >
  Design, write, change, or audit PrintStash tests. Build the required coverage matrix, choose test tiers, and use the project harness and factories.
---

# Create tests

Read the full [test-design policy](references/testing.md) and the matched
runtime references it routes to. Use [run-tests](../run-tests/SKILL.md) to pick
and execute verification lanes.

1. Extract the promised behavior from the request, issue, and owning docs.
   Build the coverage matrix before writing tests. For an uncharacterized
   refactor, establish the intended observable contract from callers and
   existing behavior, then flag discrepancies that need a decision.
2. Walk each branch of the contract, including boundaries, failures, and
   recovery. Choose the cheapest real boundary that proves each promise.
   Follow the directory tiers and [fixture policy](references/tests/fixtures.md).
3. Add or update one test per behavior. Include an e2e test of the headline
   capability for a new feature. For data-integrity or security fixes, prove
   the regression in red before editing production code. Use upstream
   [tdd](../tdd/SKILL.md) for the test-first loop.
4. Reconcile every matrix row against the assertion and its result. Use
   uncovered branches as an audit for scenarios you missed. Include the
   finished matrix in the response and the PR.

A documentation or skill change can use artifact checks for its own
deliverable. Add runtime tests where the change affects runtime behavior.
