//! Bounded triangle visibility and canonical fragment lighting; no mutable Python buffers.
#![forbid(unsafe_code)]

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

mod acquisition;
mod archive;
mod components;
mod gcode;
mod geometry;
mod image_processing;
mod mesh_prepare;
mod native_scene;
mod orchestration;
mod prepared_preview;
mod preview;
mod proximity;
mod render_job;
mod shading;
mod similarity;
mod stl;
mod stl_pipeline;
mod streaming_preview;
mod threemf;
mod threemf_archive;

const MAX_PIXELS: usize = 16_777_216;

struct Visibility {
    closest: Vec<f64>,
    winners: Vec<u32>,
    visible: usize,
    candidates: usize,
}

fn number(bytes: &[u8], itemsize: usize) -> f64 {
    if itemsize == 4 {
        f32::from_ne_bytes(bytes[..4].try_into().unwrap()) as f64
    } else {
        f64::from_ne_bytes(bytes[..8].try_into().unwrap())
    }
}

fn visibility(
    triangles: &[u8],
    itemsize: usize,
    depth: &[u8],
    width: usize,
    height: usize,
) -> Result<Visibility, &'static str> {
    let pixels = width
        .checked_mul(height)
        .ok_or("invalid image dimensions")?;
    if pixels == 0 || pixels > MAX_PIXELS {
        return Err("invalid image dimensions");
    }
    if itemsize != 4 && itemsize != 8 {
        return Err("unsupported float width");
    }
    if !triangles.len().is_multiple_of(9 * itemsize) {
        return Err("invalid triangle buffer length");
    }
    if depth.len() != pixels * 8 {
        return Err("invalid depth buffer length");
    }
    let mut closest: Vec<f64> = depth
        .as_chunks::<8>()
        .0
        .iter()
        .map(|v| number(v, 8))
        .collect();
    let mut winners = vec![u32::MAX; pixels];
    let (visible, candidates) = visibility_into(
        triangles,
        itemsize,
        &mut closest,
        &mut winners,
        width,
        height,
    )?;
    Ok(Visibility {
        closest,
        winners,
        visible,
        candidates,
    })
}

fn visibility_into(
    triangles: &[u8],
    itemsize: usize,
    closest: &mut [f64],
    winners: &mut [u32],
    width: usize,
    height: usize,
) -> Result<(usize, usize), &'static str> {
    if triangles.len() / (9 * itemsize) >= u32::MAX as usize {
        return Err("triangle index limit exceeded");
    }
    winners.fill(u32::MAX);
    let round = |v: f64| if itemsize == 4 { (v as f32) as f64 } else { v };
    let epsilon = if itemsize == 4 {
        f32::EPSILON as f64
    } else {
        f64::EPSILON
    };
    let mut candidates = 0usize;
    let mut visible = 0usize;

    for (face, bytes) in triangles.chunks_exact(9 * itemsize).enumerate() {
        let mut t = [0.0; 9];
        for (target, value) in t.iter_mut().zip(bytes.chunks_exact(itemsize)) {
            *target = number(value, itemsize);
        }
        if !t.iter().all(|v| v.is_finite()) {
            return Err("triangle coordinates must be finite");
        }
        let [ax, ay, az, bx, by, bz, cx, cy, cz] = t;
        let by_cy = round(by - cy);
        let cx_bx = round(cx - bx);
        let cy_ay = round(cy - ay);
        let ax_cx = round(ax - cx);
        let denom = round(round(by_cy * ax_cx) + round(cx_bx * round(ay - cy)));
        if !denom.is_finite() || denom.abs() <= round(1e-9) {
            continue;
        }
        let left = ax.min(bx).min(cx).floor().clamp(0.0, (width - 1) as f64) as usize;
        let right = ax.max(bx).max(cx).ceil().clamp(0.0, (width - 1) as f64) as usize;
        let top = ay.min(by).min(cy).floor().clamp(0.0, (height - 1) as f64) as usize;
        let bottom = ay.max(by).max(cy).ceil().clamp(0.0, (height - 1) as f64) as usize;
        let area = (right - left + 1) * (bottom - top + 1);
        let twice_area =
            round(round(round(bx - ax) * round(cy - ay)) - round(round(by - ay) * round(cx - ax)))
                .abs();
        let narrow = area > 256 && twice_area < area as f64 * 0.25;

        for y in top..=bottom {
            let fy = y as f64 + 0.5;
            let (mut row_left, mut row_right) = (left, right);
            if narrow {
                let mut low = f64::INFINITY;
                let mut high = f64::NEG_INFINITY;
                for (x0, y0, x1, y1) in [(ax, ay, bx, by), (bx, by, cx, cy), (cx, cy, ax, ay)] {
                    let slack = 8.0 * epsilon * y0.abs().max(y1.abs()).max(1.0);
                    if y1 != y0 && fy >= y0.min(y1) - slack && fy <= y0.max(y1) + slack {
                        let x = x0 + (fy - y0) * (x1 - x0) / (y1 - y0);
                        low = low.min(x);
                        high = high.max(x);
                    }
                }
                if low > high || high < left as f64 - 0.5 || low > right as f64 + 0.5 {
                    continue;
                }
                row_left = (low - 0.5).floor().clamp(left as f64, right as f64) as usize;
                row_right = (high - 0.5).ceil().clamp(left as f64, right as f64) as usize;
            }
            candidates = candidates.saturating_add(row_right - row_left + 1);
            for x in row_left..=row_right {
                let fx = x as f64 + 0.5;
                let w0 = (by_cy * (fx - cx) + cx_bx * (fy - cy)) / denom;
                let w1 = (cy_ay * (fx - cx) + ax_cx * (fy - cy)) / denom;
                let w2 = 1.0 - w0 - w1;
                if w0 >= 0.0 && w1 >= 0.0 && w2 >= 0.0 {
                    let z = w0 * az + w1 * bz + w2 * cz;
                    let pixel = y * width + x;
                    // Strict comparison preserves the first source face on ties
                    // and any equally close fragment from a previous chunk.
                    if z < closest[pixel] {
                        if winners[pixel] == u32::MAX {
                            visible += 1;
                        }
                        closest[pixel] = z;
                        winners[pixel] = face as u32;
                    }
                }
            }
        }
    }
    Ok((visible, candidates))
}

#[pyfunction]
fn rasterize<'py>(
    py: Python<'py>,
    triangles: &[u8],
    itemsize: usize,
    depth: &[u8],
    width: usize,
    height: usize,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    // Inputs are immutable Python bytes, never borrowed mutable NumPy storage.
    // Releasing the GIL lets job polling run during the CPU-heavy loop.
    let result = py
        .detach(|| visibility(triangles, itemsize, depth, width, height))
        .map_err(PyValueError::new_err)?;
    // Allocate the immutable result once, at its exact final size. Filling the
    // unpublished bytes directly avoids a growing Vec plus a second full copy.
    // Native-endian records: pixel u64, source face u64, depth f64.
    let output = PyBytes::new_with(py, result.visible * 24, |bytes| {
        py.detach(|| {
            let mut records = bytes.as_chunks_mut::<24>().0.iter_mut();
            for (pixel, &face) in result.winners.iter().enumerate() {
                if face != u32::MAX {
                    let record = records.next().unwrap();
                    record[..8].copy_from_slice(&(pixel as u64).to_ne_bytes());
                    record[8..16].copy_from_slice(&(face as u64).to_ne_bytes());
                    record[16..].copy_from_slice(&result.closest[pixel].to_ne_bytes());
                }
            }
        });
        Ok(())
    })?;
    Ok((output, result.candidates))
}

/// Packed final fragments: native-endian pixel u32, depth f64, then RGB u8[3].
/// MAX_PIXELS guarantees the pixel index fits in u32. Face IDs stay in Rust.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
fn rasterize_phong<'py>(
    py: Python<'py>,
    triangles: &[u8],
    itemsize: usize,
    depth: &[u8],
    normals: &[u8],
    normal_itemsize: usize,
    width: usize,
    height: usize,
    lighting: [f64; 31],
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    shading::validate(
        py,
        triangles,
        itemsize,
        normals,
        normal_itemsize,
        width,
        height,
        &lighting,
    )?;
    let result = py
        .detach(|| visibility(triangles, itemsize, depth, width, height))
        .map_err(PyValueError::new_err)?;
    let output = PyBytes::new_with(py, result.visible * 15, |bytes| {
        py.detach(|| {
            let mut outputs = bytes.as_chunks_mut::<15>().0.iter_mut();
            for (pixel, &face) in result.winners.iter().enumerate() {
                if face == u32::MAX {
                    continue;
                }
                let output = outputs.next().unwrap();
                // A stack record shares exactly the same shading arithmetic with
                // the standalone boundary, without a framebuffer-sized payload.
                let mut record = [0u8; 24];
                record[..8].copy_from_slice(&(pixel as u64).to_ne_bytes());
                record[8..16].copy_from_slice(&(face as u64).to_ne_bytes());
                shading::shade_into(
                    &record,
                    triangles,
                    itemsize,
                    normals,
                    normal_itemsize,
                    width,
                    width * height,
                    &lighting,
                    &mut output[12..],
                )
                .map_err(PyValueError::new_err)?;
                output[..4].copy_from_slice(&(pixel as u32).to_ne_bytes());
                output[4..12].copy_from_slice(&result.closest[pixel].to_ne_bytes());
            }
            Ok(())
        })
    })?;
    Ok((output, result.candidates))
}

#[pymodule]
fn printstash_mesh_native(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<acquisition::NativeDownload>()?;
    module.add_function(wrap_pyfunction!(acquisition::download_to_staging, module)?)?;
    module.add_function(wrap_pyfunction!(archive::inspect_archive, module)?)?;
    module.add_function(wrap_pyfunction!(archive::safe_entry_name, module)?)?;
    module.add_function(wrap_pyfunction!(archive::safe_subdir, module)?)?;
    module.add_class::<archive::NativeArchive>()?;
    module.add_function(wrap_pyfunction!(gcode::parse_gcode_metadata, module)?)?;
    module.add_function(wrap_pyfunction!(gcode::parse_gcode_duration, module)?)?;
    module.add_function(wrap_pyfunction!(gcode::is_bgcode, module)?)?;
    module.add_function(wrap_pyfunction!(gcode::is_valid_bgcode, module)?)?;
    module.add_function(wrap_pyfunction!(gcode::bgcode_metadata_text, module)?)?;
    module.add_function(wrap_pyfunction!(gcode::gcode_thumbnails, module)?)?;
    module.add_function(wrap_pyfunction!(render_job::render_preview, module)?)?;
    module.add_function(wrap_pyfunction!(render_job::render_views, module)?)?;
    module.add_function(wrap_pyfunction!(render_job::render_stl_fallback, module)?)?;
    module.add_function(wrap_pyfunction!(render_job::render_stl_streaming, module)?)?;
    module.add_class::<orchestration::NativeBudget>()?;
    module.add_class::<orchestration::NativeReservation>()?;
    module.add_class::<orchestration::NativeExecutor>()?;
    module.add_class::<orchestration::NativeTask>()?;
    module.add_function(wrap_pyfunction!(image_processing::process_image, module)?)?;
    module.add_function(wrap_pyfunction!(image_processing::shade_depth, module)?)?;
    module.add_function(wrap_pyfunction!(
        image_processing::normalize_thumbnail,
        module
    )?)?;
    module.add_class::<preview::NativeFrame>()?;
    module.add_class::<prepared_preview::PreparedPreview>()?;
    module.add_class::<native_scene::NativeMeshResource>()?;
    module.add_class::<native_scene::NativeScenePreview>()?;
    module.add_class::<stl_pipeline::NativeStlSource>()?;
    module.add_function(wrap_pyfunction!(stl_pipeline::sample_binary_stl, module)?)?;
    module.add_function(wrap_pyfunction!(components::component_labels, module)?)?;
    module.add_function(wrap_pyfunction!(
        streaming_preview::streaming_depth,
        module
    )?)?;
    module.add_function(wrap_pyfunction!(mesh_prepare::smooth_normals, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::voxelize, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::sh_spectrum, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::dct_hash, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::volume_inertia_ratios, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::nearest_neighbors, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::equivalent_triangles, module)?)?;
    module.add_function(wrap_pyfunction!(similarity::verification_decision, module)?)?;
    module.add_class::<proximity::SurfaceTree>()?;
    module.add("__version__", env!("CARGO_PKG_VERSION"))?;
    module.add_function(wrap_pyfunction!(rasterize, module)?)?;
    module.add_function(wrap_pyfunction!(shading::shade_fragments, module)?)?;
    module.add_function(wrap_pyfunction!(rasterize_phong, module)?)?;
    module.add_function(wrap_pyfunction!(threemf::parse_3mf_xml, module)?)?;
    module.add_class::<threemf_archive::ThreeMfArchive>()?;
    module.add_function(wrap_pyfunction!(geometry::measure_triangles, module)?)?;
    module.add_function(wrap_pyfunction!(stl::load_binary_stl, module)?)?;
    Ok(())
}
