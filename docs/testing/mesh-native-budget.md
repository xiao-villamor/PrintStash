# Native STEP worker budget — #259

The production acceptance gate exposed an OpenCASCADE parallel thread pool that
ignores OMP_NUM_THREADS. With 512 MiB of hard address space on a 12-core machine,
both STEP suffixes timed out before this fix. Serial tessellation finishes within
the same budget and preserves the mesh dimensions, topology and output protocol.
The parent still owns admission, memory, timeout and temporary cleanup.

| # | Behaviour (test name) | Category | Precondition / input | Observable outcome asserted | Tier | Status |
|---|---|---|---|---|---|---|
| 1 | fits small worker budget (TestBoundedTessellation) | Happy | Real STEP/STP, 512 MiB AS, production bootstrap/supervisor | Conversion finishes; exact extents/12 faces; no temporary residue | Integration | ✅ implemented |
| 2 | preserves worker output (TestMain) | Happy | Single mesh or placed scene | Readable exported mesh, exit 0 | Unit | ✅ implemented |
| 3 | refuses excess triangles (TestMain) | Error | Real STEP and synthetic mesh exceed cap | Exit 3; no usable output | Unit | ✅ implemented |
| 4 | refuses absent geometry (TestMain) | Error | Empty scene or point cloud | Exit 4 | Unit | ✅ implemented |
| 5 | rejects invalid invocation (TestMain) | Error | Missing arguments | Exit 2 | Unit | ✅ implemented |
| 6 | preserves native failure codes (TestBrepFailureProtocol) | Error | Missing dependency, invalid document, work limit, filesystem error, unexpected error | Existing distinct codes | Unit | ✅ implemented |
| 7 | preserves resource refusal (TestNativeResourceFailure) | Error | MemoryError or ENOMEM in B-rep conversion | Allocation failure reaches worker resource classification | Unit | ✅ implemented |
| 8 | refuses failed conversion (TestNativeResourceFailure) | Error | Nonzero native conversion status | Exit 4; partial intermediate cleaned; no output | Unit | ✅ implemented |
| 9 | recovers on small machines (Gate.run) | Edge | Shipped full image in 1 GiB container | Native conversion settles; API survives; next healthy file ready | Production container | ❌ pending final image run |

The container row becomes complete only when its resource report passes.
