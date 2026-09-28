# CI and image publication

`CI` runs on every pull request, merge queue commit and push to `main`. Its
backend shards cover each test file once, excluding `slow` and tests needing
container-backed services. Core, frontend, extension and two real-backend
browser flows run in parallel. Configure branch protection to require **PR
gate** only: it fails if any of those jobs fails or is skipped. Do not add path
filters to this required workflow.

Migration upgrade tests run in three alphabetic shards so a slow runner does
not cancel the entire suite at the 15-minute per-job limit. The repository
test suite checks that every backend test file belongs to exactly one shard.

`Deep CI` runs nightly or by manual dispatch. It checks branch coverage and
area/module floors, external provider contracts, Python/native compatibility,
the full browser configurations and the extension's real backend and
ChromeDriver flows. Before tagging a release, dispatch it on the exact `main`
commit to be tagged and wait for success. Release publication requires green
`CI` and `Deep CI` runs for that SHA. Nightly and manual `latest` publication
require a green `CI` run for the same SHA on `main`.

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
