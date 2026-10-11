# PRI-16 browser development comparison

Application baseline: 8d70c556 (Three.js 0.182.0).
Candidate: 42bb966836b04d4f35ca1ddeae3eca531b6d5706 (Three.js 0.186.1).
Browser: native Windows Chrome 154.0.8037.98, available RTX 5060.
Both worktrees use their frozen dependency locks. The baseline uses the
candidate's standalone measurement fixture; baseline production source is
unchanged. The fixture hash is frozen in the report manifest.

Each workload/backend comparison declares thirty attempts before output.
Fresh browser contexts alternate baseline/candidate order against warm Vite
servers. Both implementations produce the same SHA-256 source bytes for
each workload. Viewport is 900x700, canvas 640x480, device scale one.
Each attempt records 120 animation-frame intervals while alternating fit/zoom
and a PNG capture. All raw attempts, failures, frame intervals, dispersion and
bootstrap confidence intervals are retained in browser-42bb9668.json.

| Workload / renderer | Completed / attempted | Median readiness proxy (ms) | Median observation p95 frame interval (ms) | Median capture (ms) |
| --- | ---: | ---: | ---: | ---: |
| Box / baseline WebGL | 30 / 30 | 472.25 | 7.10 | 52.25 |
| Box / candidate WebGPU | 30 / 30 | 736.30 | 7.10 | 61.30 |
| 262,144-face torus / baseline WebGL | 30 / 30 | 632.35 | 7.10 | 52.70 |
| 262,144-face torus / candidate WebGPU | 29 / 30 | 1015.40 | 7.10 | 64.30 |

Candidate torus attempt 25 failed because the browser execution context was
destroyed. The raw error remains in the report; its root cause is not
established. It must not be discarded or silently retried as a success.

**Decision: keep WebGPU experimental and WebGL the default.** This comparison
does not demonstrate the required interaction improvement. Readiness is measured
as the ready callback plus two animation frames, not actual first visible paint.
It cannot qualify the first-frame gate. Other development validation overlapped
the run; timings are descriptive, not isolated performance acceptance. These
two controls also do not replace the full visual-parity corpus.

For reproduction, place browser-controls.mjs beside a playwright-core package
and use native Chrome at the path declared in that probe. Serve the baseline on
localhost:3291 and candidate on localhost:3290, using the same committed
mesh-benchmark fixture in both. The probe refuses an existing output directory.
Change commit identifiers and fixture hash before using it for another revision.
Windows executable locations are environment-specific development setup.
