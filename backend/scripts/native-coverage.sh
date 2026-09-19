#!/usr/bin/env bash
# Rust source coverage, including the actual PyO3 entry points exercised by Python.
# Requires cargo-llvm-cov 0.9.1, llvm-tools-preview, and the backend dev environment.
set -euo pipefail

backend=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$backend"
export RUSTUP_TOOLCHAIN="${RUSTUP_TOOLCHAIN:-1.98.1}"
export CARGO_TARGET_DIR="$backend/rust/target/native-coverage"
output="$backend/rust/target/native-coverage-report"
mkdir -p "$output"

if [[ "$(cargo llvm-cov --version)" != "cargo-llvm-cov 0.9.1" ]]; then
  echo 'Install cargo-llvm-cov 0.9.1 with --locked before collecting coverage.' >&2
  exit 1
fi

# Instrument a separate build; never replace the installed release extension.
coverage_env=$(cargo llvm-cov show-env --manifest-path rust/Cargo.toml --sh)
eval "$coverage_env"
cargo llvm-cov clean --manifest-path rust/Cargo.toml --workspace
cargo build --manifest-path rust/Cargo.toml --locked --features pyo3/extension-module

bindings=$(mktemp -d)
trap 'rm -rf "$bindings"' EXIT
case "$(uname -s)" in
  Linux) library="$CARGO_TARGET_DIR/debug/libprintstash_mesh_native.so" ;;
  Darwin) library="$CARGO_TARGET_DIR/debug/libprintstash_mesh_native.dylib" ;;
  *) echo 'Native coverage currently supports Linux and macOS.' >&2; exit 1 ;;
esac
cp "$library" "$bindings/printstash_mesh_native.so"
export PYTHONPATH="$bindings:$backend:$backend/packages/printstash-core/src${PYTHONPATH:+:$PYTHONPATH}"
export PRINTSTASH_COVERAGE_BINDING="$bindings/printstash_mesh_native.so"
python_bin="${PRINTSTASH_TEST_PYTHON:-$backend/.venv/bin/python}"
"$python_bin" -c 'import os, printstash_mesh_native as native; assert native.__file__ == os.environ["PRINTSTASH_COVERAGE_BINDING"], native.__file__'
"$python_bin" -m pytest -q --no-cov rust/tests
# The app and autonomous core have distinct packages both named `tests`.
# Run each in its own interpreter, preserving the same native profile sink.
(cd packages/printstash-core && "$python_bin" -m pytest -q --no-cov tests/mesh tests/gcode)
"$python_bin" -m pytest -q --no-cov tests/integration/modules/media/test_mesh_render.py \
  tests/integration/modules/media/test_mesh_processing.py::TestLoadMesh \
  tests/unit/modules/media/test_bgcode.py \
  tests/contract/api/v1/test_ingest.py::TestDownloadToStaging
cargo test --manifest-path rust/Cargo.toml --locked \
  --package printstash-acquisition-core \
  --package printstash-gcode-core --package printstash-libbgcode \
  --package printstash-similarity-core
cargo test --manifest-path rust/render-core/Cargo.toml --locked
report_args=(
  --manifest-path rust/Cargo.toml
  --package printstash-mesh-native
  --package printstash-acquisition-core
  --package printstash-gcode-core
  --package printstash-libbgcode
  --package printstash-render-core
  --package printstash-similarity-core
)
cargo llvm-cov report "${report_args[@]}" --json --output-path "$output/coverage.json"
"$python_bin" - "$output/coverage.json" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1]))
files = [file for entry in report["data"] for file in entry["files"]]
for source in (
    "/rust/src/",
    "/rust/acquisition-core/src/",
    "/rust/gcode-core/src/",
    "/rust/libbgcode-sys/src/",
    "/rust/render-core/src/",
    "/rust/similarity-core/src/",
):
    measured = [file for file in files if source in file["filename"]]
    if not measured or not sum(file["summary"]["lines"]["covered"] for file in measured):
        raise SystemExit(f"Missing executed native coverage for {source}")
PY
cargo llvm-cov report "${report_args[@]}" --lcov --output-path "$output/lcov.info"
cargo llvm-cov report "${report_args[@]}" > "$output/summary.txt"
cat "$output/summary.txt"
rustc --version --verbose > "$output/toolchain.txt"
cargo llvm-cov --version >> "$output/toolchain.txt"
