# M02 dependency preflight — 2026-09-17

M02 updates the native workspace inherited from M00 and retained unchanged by
M01 to the latest stable compatible releases and uses the latest patched stable
compiler, Rust 1.98.1. Rust 1.98.1 supersedes 1.98.0 because it fixes an
upstream vtable miscompilation that could produce undefined behavior.

| Dependency | M00/M01 | Current stable | License | Probe result |
| --- | --- | --- | --- | --- |
| PyO3 | 0.28.3 | 0.29.2 | MIT OR Apache-2.0 | Compiles and Rust tests pass after the documented 0.28→0.29 migration; retain `abi3-py311`. This release fixes two upstream-reported security vulnerabilities. |
| quick-xml | 0.38.3 | 0.42.0 | MIT | Compiles and Rust tests pass after changing tag/attribute handling from bytes to strings and using bounded XML 1.0 attribute normalization. 0.41 introduced upstream parser security fixes. |
| image | 0.25.9 | 0.25.10 | MIT OR Apache-2.0 | Compiles and Rust tests pass with existing PNG/WebP/JPEG-only features. |
| zip | 8.6.0 | 8.6.0 | MIT | Already current stable; 9.0.0-pre3 is prerelease. |
| flate2 | 1.1.10 | 1.1.10 | MIT OR Apache-2.0 | Already current. |
| petgraph | 0.8.3 | 0.8.3 | MIT OR Apache-2.0 | Already current. |
| rayon | 1.12.0 | 1.12.0 | MIT OR Apache-2.0 | Already current. |
| fast_image_resize | 6.1.0 | 6.1.0 | MIT OR Apache-2.0 | Already current. |

The security statements above come from the maintainers' release records:
[PyO3 0.29](https://github.com/PyO3/pyo3/releases/tag/v0.29.0) fixes the two
reported soundness issues, and
[quick-xml 0.41](https://github.com/tafia/quick-xml/releases/tag/v0.41.0)
bounds namespace declarations and removes quadratic duplicate-attribute checks.

The Python build backend moves from Maturin 1.9.6 to the current stable 1.15.0
and is pinned exactly in both `pyproject.toml` and native container builder
stages. The release is published through PyPI trusted publishing and remains
MIT OR Apache-2.0.

`cargo update` also selected current compatible transitive patches including
`cfg-if` 1.0.5, `moxcms` 0.8.1, `syn` 3.0.6, `unicode-ident` 1.0.26, and
`zlib-rs` 0.6.8. A fresh registry update found zero newer compatible locked
packages. The probe used exact direct requirements and a regenerated lockfile.
The duplicate-tree audit found no duplicate direct requirement. The remaining
parallel transitive lines are owned by upstream version ranges: `hashbrown`
through petgraph/indexmap, `miniz_oxide` through PNG/flate2, and `syn` through
PyO3/thiserror. M02 does not force unsupported unification to make the lockfile
look smaller.

M02 pins the production workspace with `rust-toolchain.toml` at Rust 1.98.1 and
pins both native container builder stages to the official multi-architecture
`rust:1.98.1-slim-bookworm` index digest
`sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57`.
The index contains native amd64 and arm64 manifests, so the existing native
architecture jobs retain real architecture builds rather than emulation.
The reusable mesh and render crates keep their declared Rust 1.88 minimum;
their public library compatibility does not need to rise merely because
PrintStash production builds use the patched 1.98.1 compiler. The isolated
qualification crate declares Rust 1.98 because it is application test tooling,
not a reusable dependency.

Preflight verification completed against the integrated M01 tree:

- Cargo check for all targets on Rust 1.98.1.
- Rust 1.98.1 Clippy with warnings denied.
- Replaced fixed-width `chunks_exact` conversions with stable `as_chunks`
  slices across the binding and render core, as required by Rust 1.98.1's
  stricter Clippy checks; no lint allowance was added.
- Rust tests with the repository's Python 3.11 interpreter selected for PyO3.
- An optimized `cp311-abi3` wheel built through Maturin 1.15.0 and all 290
  Python binding tests passed when that wheel was loaded by Python 3.11,
  including an XML numeric-character-reference regression test for the
  quick-xml migration.

CI also runs pinned cargo-audit 0.22.2 against the production lockfile before
native coverage. Container Grype scans and the exact-diff security review remain
separate gates because a Rust advisory check does not cover system or Python
packages.

M02 still requires Python binding tests, native coverage, both architectures,
container builds, the full application CI matrix, security scan, and controlled
before/after performance evidence on its committed revision.
