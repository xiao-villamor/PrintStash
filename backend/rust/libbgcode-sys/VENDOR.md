# Vendored native sources

This crate builds the established Prusa BGCODE implementation behind a narrow
safe Rust API. The vendored directories are immutable upstream source snapshots;
PrintStash-specific bounds and PyO3 translation live outside them.

## libbgcode

- Upstream: `https://github.com/prusa3d/libbgcode`
- Commit: `d4da9073616d70a43c151e8c1d7fbff879d2e08a`
- Current upstream `main`/`HEAD` verified: 2026-09-18
- Upstream version constant: `0.3.0`
- Source archive SHA-256: `d26778992d44b7cfaab99b542d871355f7a66210a512a5503a5b8a941a7d409b`
- License: AGPL-3.0-only (`vendor/libbgcode/LICENSE`)
- Included source: `core/` framing/checksum code and the upstream license
- Local modifications inside `vendor/libbgcode`: none

Upstream publishes no release tags. The full 40-character commit is therefore
the version identity. Updating it requires recording the new archive hash,
reviewing the source diff, rebuilding both supported architectures, and running
the malformed-container, compression, checksum, metadata, thumbnail, image, and
performance gates.

## Heatshrink

- Upstream: `https://github.com/atomicobject/heatshrink`
- Release: `v0.4.1`
- Latest stable upstream tag verified: 2026-09-18
- Source archive SHA-256: `2e2db2366bdf36cb450f0b3229467cbc6ea81a8c690723e4227b0b46f92584fe`
- License file SHA-256: `f41cc7241aea5951c2834ca86050c6bf094b223597830ab6e7b040d0dbd131df`
- License: ISC (`vendor/heatshrink/LICENSE`)
- Included source: decoder C source and required public headers
- Local modifications inside `vendor/heatshrink`: none

Heatshrink is the codec selected by libbgcode for compression modes 11/4 and
12/4. Its decoder is compiled as C by `cc`; the algorithm is not reimplemented
in the adapter.

The unused upstream binarizer, MeatPack decoder, and Heatshrink encoder do not
enter the wheel. Ordinary import metadata never decodes the printable G-code
body, and PrintStash's separately supervised official CLI owns toolpath
conversion. libbgcode's higher-level metadata decoder is also unsuitable for
untrusted uploads: it allocates from declared output sizes, does not verify the
exact Heatshrink output length, and its deflate loop does not enforce forward
progress after stream end. The adapter therefore applies fixed budgets and exact
output checks around the established zlib and Heatshrink decoder APIs while
retaining libbgcode's upstream header, parameter, version, and checksum logic.

## Build dependencies

The adapter uses CXX for the Rust/C++ boundary and `libz-sys` for stock zlib.
Their exact versions and registry checksums are owned by the workspace
`Cargo.toml` and `Cargo.lock`. No system CMake or zlib development package is
required for an ordinary Cargo or Maturin build.

Direct crates were rechecked against crates.io on 2026-09-18:

| Dependency | Version / features | License | Role |
| --- | --- | --- | --- |
| `cxx` / `cxx-build` | `1.0.202`, default (`std`) | MIT OR Apache-2.0 | Safe generated Rust/C++ bridge |
| `libz-sys` | `1.1.29`, default + `static` | MIT OR Apache-2.0; bundled zlib 1.3.2 uses the Zlib license | Stock deflate implementation with no runtime zlib dependency |
| `cc` | `1.4.6`, default | MIT OR Apache-2.0 | Compile the pinned C/C++ sources |
| `regex` | `1.13.1`, default | MIT OR Apache-2.0 | Text G-code field policy in `gcode-core` |

`crc32fast` 1.5.2 and `flate2` 1.1.10 with only `zlib-rs` are test-only. They
construct independent fixtures and do not enter the production wheel graph.
The new production graph adds no duplicate direct dependency; its parallel
`hashbrown`, `foldhash`, and `syn` transitive lines come from the existing
petgraph/PyO3 graph and CXX's current supported ranges.
