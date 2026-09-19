# Establish the task baseline

Run this before implementation, after context recovery, when the user names a
different destination branch, and before commit, push or deployment.

## Select the branch before selecting the implementation

1. Inspect `git status --short --branch`, `git worktree list --porcelain`, local
   branches and their upstreams. Refresh configured remotes before treating
   remote-tracking refs as current. Inspect recent commits on branches and
   worktrees that already own the requested feature.
2. The user's named branch is the destination. Otherwise continue the branch
   that contains the active feature. The shell's current directory and a
   recovery note are evidence, not authority to choose another branch. If two
   active implementations leave the destination materially ambiguous, ask that
   one question while continuing read-only investigation.
3. Use the destination's existing worktree when available. Preserve its dirty
   changes and ownership boundaries. For a genuinely new feature, create the
   short-lived branch from updated `main` following [conventions](conventions.md).
   When carrying local work across branches, capture its baseline, isolate the
   task's patch, compare both histories, and integrate it without replacing
   newer destination implementations or publishing unrelated changes.
4. In the selected checkout, run:

   ```bash
   python3 scripts/check_worktree.py --branch <destination> --fetch
   ```

   Use `--remote <name>` when the destination uses another remote. Omit `--fetch`
   only for a repository with no configured remote or an explicitly offline task;
   record that remote freshness is unverified. A failed fetch is not fresh state.
   Wrong branch, detached HEAD, or unintegrated remote commits must be resolved
   before implementation. The command never switches, merges, resets or stages.
   A dirty result requires preserving the baseline, not cleaning the checkout.
5. Record the absolute checkout, destination branch, starting HEAD, fetched
   remote HEAD, initial dirty paths and owned scope in local task notes. State
   the branch and starting commit in the first implementation update. Start
   editing only after those identities agree with the intended task.

## State the execution architecture

For Rust migrations, ingestion stages, queues or performance redesigns, trace
the destination's actual call path before choosing a design. Record each stage's
compute implementation, coordinator, durable-state owner and acceptance test.
Inspect existing native executors, admission controls, required-engine policies
and recently removed fallbacks before adding another execution path.

Moving a stage into a background Python task does not migrate its computation
or scheduling to Rust. State explicitly what moves to Rust, what remains in
Python, and why. Match the user's requested scope; raise a material ambiguity
early. Report remaining Python work at handoff, including work that bypasses an
existing native executor. Implementation tests must exercise the chosen engine.

## Revalidate before publishing

Repeat the branch check after resuming and before publishing. Compare the saved
baseline with the current destination; account for intervening commits before
reusing tests, security reviews or measurements. After integration, validate the
combined implementation and its changed contracts. Earlier benchmarks remain
historical evidence until repeated on the new implementation.

Build deployments from the exact committed source, preserving the running
configuration and data volumes. Record the commit and image IDs, then verify
the published remote commit, migrations and HTTP health after rollout. Prepare
required builds and verification before replacing the service. Run timing-sensitive
UI tests and performance benchmarks without concurrent compilation or heavy suites.
