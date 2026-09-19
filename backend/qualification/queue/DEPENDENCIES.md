# Qualification dependency record

Checked against crates.io on 2026-09-17. `Cargo.lock` is the executable source
of truth; every direct requirement is exact and CI uses `--locked`.

| Package | Selected | Current stable | Reason / features |
| --- | --- | --- | --- |
| `apalis` | 0.7.4 | 0.7.4 | Defaults disabled; public worker and storage traits only. Registry checksum `504d52557a16b7b202941660f339c9182910d498cac40f93d15f6b31e6bef290`; MIT OR Apache-2.0. 1.0.0-rc.10 is prerelease. |
| `apalis-sql` | 0.7.4 | 0.7.4 | `migrate`, `postgres`, `sqlite`, `tokio-comp`; checksum `9d96124556e2190523c1a52acf3b3e02af564343b4fa888f0b712775ac80ee04`; MIT. |
| `azums` | 1.0.1 | 1.0.1 | Defaults disabled; `postgres` and `sqlite` only, leaving Redis and CLI out; checksum `c9a2d77fb435b959d88f4b694a010a8c9e3f932e5de8d5f8a2caa4b7056af41e`; MIT OR Apache-2.0; MSRV 1.88. |
| `sqlx` | 0.8.6 | 0.9.0 | Latest release in both candidates' compatible 0.8 line. Forcing 0.9 would be outside their declared ranges. Defaults disabled; runtime uses Tokio + rustls. |
| `tokio` | 1.53.1 | 1.53.1 | Only I/O, macros, process, multi-thread runtime, synchronization, and time features. |
| `uuid` | 1.26.1 | 1.26.1 | Serialization and v4 IDs for isolated queue/worker generations. |
| `anyhow` | 1.0.104 | 1.0.104 | Qualification CLI and scenario context only. |
| `cpu-time` | 1.0.0 | 1.0.0 | Established process CPU clock wrapper used to isolate idle polling overhead; MIT OR Apache-2.0. |
| `serde` | 1.0.229 | 1.0.229 | Derive support for versioned evidence. |
| `serde_json` | 1.0.151 | 1.0.151 | Sanitized evidence and candidate payloads. |

The production compiler is Rust 1.98.1. CI pins cargo-audit 0.22.2,
cargo-deny 0.20.2, and cargo-llvm-cov 0.9.1, the stable registry releases at
the same check date. The dependency graph is qualification-only and does not
enter the native extension, Python wheel, or application containers.

`cargo-audit` reports RUSTSEC-2023-0071 for `rsa` 0.9.10 because
`sqlx-mysql` remains in the resolved lockfile. The harness disables MySQL,
`cargo tree -i rsa` confirms that `rsa` is absent from the compiled graph, and
the advisory has no fixed release. CI therefore names and ignores only this
inactive lockfile advisory; every other advisory remains fatal. Revisit the
exception when either candidate moves beyond SQLx 0.8.6.

The test-only benchmark image pins the official multi-architecture
`rust:1.98.1-slim-bookworm` index at
`sha256:ebd900bae66fd508b466cef82d64a83a5fb34682e4c8b2797a42908bddc95a57`
and `debian:bookworm-slim` at
`sha256:88200866dfff7ea7f5cbcb6ec7c8a701889efe6fe859fe64d6990e4b07ea4171`.

Effectum is not compiled because it has no PostgreSQL backend. Fang is not
compiled because its documented interrupted-job recovery behavior fails the
selection prerequisite. Neither exclusion requires application integration
code to prove a missing advertised capability.
