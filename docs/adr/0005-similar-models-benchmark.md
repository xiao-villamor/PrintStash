# Similar Models resource measurements

Measured on 10 September 2026 against `geometry-v2-sh5f4577c4`, Linux x86_64,
4 logical CPUs. These results describe this host, not a Raspberry Pi or NAS
certification. The commands produce machine-readable reports and fail when their
acceptance assertions fail.

## 10,000-Artifact prepared index

```sh
uv run --directory backend python -m tests.fakes.similarity_benchmark \
  --root /tmp/printstash-similarity-benchmark --count 10000 --designs 32
```

The fixture measures 32 geometry variants derived from eight real base files:
the repository's Calibration Cube and Spatula plus the six attributed Thingi10K
fixtures. Unique STL re-exports populate 10,000 Artifacts using these fingerprints;
this is a prepared index with one uncached Artifact in the requested Model scope.
It is not a cold backfill of 10,000 independently analyzed designs.

| Measurement | Result |
| --- | ---: |
| Geometry analysis for the 32 variants | 96.731 s |
| Prepared population, including durable commits | 1,076.682 s |
| Initial response from cached evidence | **0.154 s**, 1 candidate |
| First newly verified candidate | 14.841 s |
| Complete requested Model scope | 304.543 s |
| Final Run state | completed |
| Verified pairs / resulting candidates | 80 / 80 |
| Proposals omitted by the shortlist budget | 3,772 |
| Peak process RSS | 430,043,136 bytes |
| SQLite database after checkpoint | 205,504,512 bytes |
| Fingerprints, including components | 38,752 |
| Component fingerprints | 28,751 |
| Descriptor BLOB payload | 21,701,120 bytes |
| Similarity tables and indexes per component | 6,747.98 bytes |

The initial response clears the plan's two-second target on this host. Completion
is measured separately; dense duplicate buckets deliberately exceed the shortlist
budget, so the result does not establish exhaustive recall. The report includes
individual SQLite table/index page sizes, input hashes and variant-analysis times.
Preparing cached rows took longer than the subsequent requested scope. No full
cold-library backfill time or independent-design recall is claimed.

## Verification while a thumbnail is rendering, under a 1 GiB limit

```sh
systemd-run --user --scope -p MemoryMax=1G -p MemorySwapMax=0 --quiet \
  uv run --directory backend python -m tests.fakes.similarity_resource_probe \
  --output /tmp/printstash-similarity-resource.json
```

The probe resolves the real Spatula 3MF into an analysis-only temporary STL,
verifies 5,000 independently sampled points, and concurrently renders a 128-pixel
Calibration Cube thumbnail. The input meshes remain unchanged and the temporary
analysis directory is removed.

| Measurement | Result |
| --- | ---: |
| Cgroup memory limit | 1,073,741,824 bytes |
| Cgroup peak charged memory | 113,254,400 bytes |
| Process peak RSS | 170,074,112 bytes |
| Process initial RSS | 51,675,136 bytes |
| Elapsed time | 10.049 s |
| Sample count | 5,000 |
| Evidence Class | identical_geometry |
| Thumbnail output | 9,955 bytes |
| Configured render concurrency | 1 |
| Temporary directory removed | yes |

Cgroup charged memory and process RSS are different accounting measures, so both
raw values are retained. Spatula has 6,704 faces: this verifies the specified
concurrent operations with real fixtures, not the maximum admitted 200,000-face
case. Separate worker containment tests cover timeout and memory-budget exits.
