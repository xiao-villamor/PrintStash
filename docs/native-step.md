# Native STEP helper qualification

M11 requires a supervised Rust helper that replaces Python execution without
changing PrintStash's STEP results or resource limits. The existing disposable
worker remains authoritative because no evaluated Rust integration qualifies.
This is a recorded blocker, not a claim that M11 is complete.

## Ownership while M11 is blocked

| Concern | Owner |
| --- | --- |
| STEP import, unit conversion, B-rep checks, topology, volume, and tessellation | Open CASCADE through the current Python OCP worker |
| Worker timeout, RSS ceiling, process termination, and capacity reservation | Python media coordinator |
| Analysis generations, durable jobs, and Artifact publication | Existing Python application transactions |

The current worker explicitly reads source units, selects millimetres as the
system unit, uses 0.05 mm linear and 0.35 radian angular deflection, disables
relative and parallel meshing, applies assembly placements, validates the B-rep,
and checks topology, vertex, and triangle ceilings before returning bounded
NumPy arrays. Its process boundary already contains crashes, timeouts, and RSS
growth. It therefore remains safer than replacing it with an unqualified helper.

## Libraries evaluated

### `occt-wasm` 3.3.2 — rejected

The exact [published crate](https://crates.io/crates/occt-wasm/3.3.2) embeds an
Open CASCADE WebAssembly module and exposes STEP import, topology, validity,
volume, bounding-box, and tessellation operations through Wasmtime. It is the
closest maintained library to the required helper interface and avoids a custom
C++ binding. The crate requires Rust 1.95, uses Wasmtime 48.0.2, and ships under
MIT or Apache-2.0; its embedded OCCT module has separate LGPL-2.1 obligations.

A functional release probe used the exact published package, a generated
10×20×30 mm STEP box, and the required 0.05/0.35 tessellation settings. Kernel
construction failed before STEP import with:

```text
unknown import: env::emscripten_get_preloaded_image_data has not been defined
```

Version 3.3.2 therefore still does not initialize through its documented API.
Adding a private Emscripten compatibility layer or maintaining a patched WASM
artifact would move dependency maintenance into PrintStash and is outside the
accepted architecture. No performance benchmark was run: the candidate did not
reach geometry production, so timing it would not be acceptance evidence.

### `opencascade` 0.3.0 — rejected

The [`opencascade` crate](https://crates.io/crates/opencascade/0.3.0) provides a
high-level Rust API, but its default build pins bundled OCCT 7.8.1 while the
current application uses the newer OCP/OCCT line. The project describes itself
as work in progress. Dynamic linking requires separately packaged OCCT
development libraries that the supported OCP wheel does not provide. Adopting
the older bundled kernel or adding a second system OCCT distribution without
first proving unit, assembly, topology, volume, and tessellation parity does not
qualify.

### `occt-rs` and parser-only crates — rejected

`occt-rs` has no stable API and uses AGPL-3. Parser-only crates such as `stepq`
do not supply unit conversion, B-rep validation, topology, volume, or
tessellation. They cannot replace the current worker and would force
PrintStash to build a CAD kernel or binding.

## Acceptance matrix

| # | Behaviour | Category | Required input | Observable qualification result | Status |
| --- | --- | --- | --- | --- | --- |
| 1 | initializes within the local worker contract | Edge | Release helper under supported toolchain | Kernel starts and accepts a STEP document | ❌ `occt-wasm` fails before initialization completes |
| 2 | preserves millimetre box geometry | Happy | 10×20×30 mm STEP box | 6/12/8/1 topology, 6000 mm³ volume, watertight outward mesh | ❌ candidate produces no geometry |
| 3 | converts declared metres to millimetres | Happy | `cascadio_material.stp` | Existing extents, source-unit evidence, and volume parity | ❌ candidate cannot import the fixture |
| 4 | preserves assembly placements | Happy | Two boxes separated by 50 mm | 60×20×30 mm extents, two solids, 12000 mm³ | ❌ candidate cannot import the fixture |
| 5 | enforces geometry work limits | Error | Triangle/topology overflow | Controlled `geometry_work_limit` without oversized output | ❌ helper integration never reaches limit enforcement |
| 6 | contains malformed input and process failure | Error | Invalid STEP, timeout, RSS breach | Stable failure code, terminated child, released capacity | ❌ candidate cannot pass the happy-path prerequisite |

## Re-entry gate

Revisit M11 only when an established library or official helper can initialize
inside the local worker contract and expose source-unit handling, STEP transfer,
placements, topology, validity, volume, and bounded tessellation without a
PrintStash-maintained CAD binding. Qualification must run the existing STEP
integration corpus first, then cold/warm complex-assembly benchmarks and full
packaging tests on amd64 and arm64. Until then, Python remains the coordinator
and execution language for this stage, and the optional STEP dependency remains
isolated from lite installations.
