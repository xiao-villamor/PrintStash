# CI and image publication

`CI` runs on every pull request, merge queue commit and push to `main`, and can
be dispatched on an integration branch. Its
backend shards cover each test file once, excluding `slow` and tests needing
container-backed services or exclusive host-native measurements. Core, frontend, extension and two real-backend
browser flows run in parallel. Configure branch protection to require **PR
gate** only: it fails if any of those jobs fails or is skipped. Do not add path
filters to this required workflow.

Migration upgrade tests run in three alphabetic shards so a slow runner does
not cancel the entire suite at the 15-minute per-job limit. The repository
test suite checks that every backend test file belongs to exactly one shard.

`Deep CI` runs nightly or by manual dispatch. It checks branch coverage and
area/module floors, the `scale` lane (library reads timed at 25,000
collections and 100,000 Models in four 13-case jobs, each capped at 30 minutes), external provider
contracts, Python/native compatibility, the full browser configurations and the
extension's real backend and ChromeDriver flows. Before tagging a release, dispatch it on the exact `main`
commit to be tagged and wait for success. Release publication requires green
`CI` and `Deep CI` runs for that SHA. Nightly and manual `latest` publication
require a green `CI` run for the same SHA on `main`.

Python compatibility runs the `full-ordinary` and `full-resources` lanes in
independent jobs. The ordinary phase includes slow tests and retains work-stealing;
the resource phase runs serially so each real service starts once. Tests marked
`native_host` also run in that mandatory phase: independent test vaults have
independent admission pools, so an xdist companion would consume CPU/RAM outside
the pool whose wall-clock bounds are being measured. Fairness still runs real
DBOS/native workers with the same budgets, six arrivals and 110/130-second limits.
Coverage appends that serial phase before evaluating its unchanged floors. Their marker
sets are disjoint and together cover the full lane, excluding the separately
measured scale and coverage gates. Both phases must succeed; fail-fast is disabled
so one failure preserves the other result. Each retains the 60-minute limit.
The `full` lane and backend branch-coverage job keep their existing composition.

The `scale` lane runs serially within each CI job. Four separate jobs split
budget and growth checks by administrator and granted viewer, measuring 13
reads apiece. Running four seeded 100,000-Model databases on one CI runner
exhausted its 30-minute cap, even after the suite was split across jobs.
The lane and each job target the scale test module directly, so pytest does not
collect unrelated backend tests. Jobs report the active case and per-case
durations, and print a Python stack trace if a case waits two minutes.

Publication builds all four images in one Bake graph per native architecture.
Each architecture smokes its four digests; promotion to multiarch tags starts
only after both architecture jobs succeed.

## Timing baseline and targets

On 2026-09-28, the last ten successful PR `CI` runs available from the public
Actions API had a median of **40.5 minutes** from the first job start to the
last job finish and **114.4 runner minutes** summed across successful jobs.
The backend test step had a median of **40.1 minutes**. Runs sampled:
`36341399550`, `36273116954`, `36257674750`, `36253307830`,
`36252676660`, `36241073402`, `36241065027`, `36241056769`,
`36238477203`, `36238425612`.

The former canary run [36350313500](https://github.com/xiao-villamor/PrintStash/actions/runs/36350313500)
on 2026-09-27 failed after **2 h 24 min**: its old nested CI's backend
step took **51 min 39 s**, while the five-ordering flaky-detection step ran
**2 h 22 min 22 s** before failing. All image publication was consequently
skipped. Neither step is part of the nightly prerequisite; the nightly workflow
reads the already completed quick CI result for its `main` SHA.

Compare ten successful PR runs after rollout using the same Actions jobs API
(`GET /repos/{owner}/{repo}/actions/runs/{run_id}/jobs?per_page=100`). Exclude
queue time: measure from the earliest job `started_at` to the latest required
job `completed_at`. Sum each job's duration for runner minutes. Inspect step
`started_at`/`completed_at` to find the bottleneck. The required gate target is
**15 minutes or less**. For an image publish with a warm cache, measure the
build jobs' first start through the last manifest job's completion; target
**30 minutes or less**. If either target is missed, optimize the measured slow
step before treating the rollout as complete.

The public history did not contain ten successful comparable pre-release image
publications at the time of this baseline. Record the first nightly run under the
new workflow, then compare subsequent hot-cache runs using the same timestamps.


## Mesh resource gate

Deep CI builds the actual full production image on amd64 and arm64 and runs
the real API and DBOS engine with 1 GiB and 4 GiB cgroup ceilings. The gate
fails on OOM kills, unresponsive API, renewed terminal work, leaked workers,
unexplained staging/capacity reservations, or parent RSS growth after warm-up.
It exercises synthetic nested 3MF expansion, malformed packages, sudden
allocation, native STEP/STP, healthy successors, original/slicer downloads,
viewer conversion, restart and retained-input discard. Each job publishes its
JSON measurements and cleanup evidence even on failure.

See [the acceptance matrix](testing/mesh-regression.md) for the commands and
optional local acceptance of the issue attachments. CI has no download
dependency on those attachments.
