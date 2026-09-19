# PrintStash render core

The backend renderer runs in this crate without Python, NumPy, Pillow, a display,
or a graphics driver. The parent crate provides PyO3 transport adapters.

`job::render` accepts mesh buffers and the versioned preview recipe. Rust owns
normal preparation, camera selection, lighting, projection, depth resolution,
anti-aliasing, vignette, and PNG/WebP encoding. RGB output composites onto white
inside the same job for visual-search inference, without an intermediate codec.

`streaming_job::render` reads binary or ASCII STL in bounded chunks. It owns both
passes, deterministic framing samples, source identity checks, coverage limits,
and depth shading. `fallback::render` uses bounded stratified binary reads or an
ASCII prefix and reports whether that source sample is complete. Neither job
calls Python between chunks. Stage timings are returned with successful results.

Image codecs use `image`; resampling uses `fast_image_resize`. The existing
alpha arithmetic and convolution pass order are preserved. Rendering stays
single-threaded within a job so the application's resource-aware executor can
limit concurrent jobs without nested thread pools. Buffers are owned by the job
and released before its result is handed back.

Python still owns application requests, process supervision, storage, and the
import workflow. Full-mesh loading for OBJ/3MF/STEP and STEP tessellation remain
outside this crate. STEP conversion still uses OpenCASCADE through Cascadio.
The browser continues to use Three.js.

Backend source installs build the required PyO3 extension during `uv sync`.
Install Rust and Cargo first; the native CI and container build use Rust 1.98.1.
Container runtime stages install the wheel produced by the native build stage.
The standalone crate itself does not require Python.

```sh
cargo test --manifest-path backend/rust/render-core/Cargo.toml --locked
```
