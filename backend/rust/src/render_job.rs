//! A single Python boundary around a complete Rust rendering job.
use printstash_render_core::job::{self, Profile, RenderOptions};
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

#[pyfunction]
#[pyo3(signature = (vertices, faces, width, height, chunk, format, recipe, supersampling, rotation=None, matte=false))]
#[allow(clippy::too_many_arguments)]
pub fn render_preview<'py>(
    py: Python<'py>,
    vertices: &[u8],
    faces: &[u8],
    width: usize,
    height: usize,
    chunk: usize,
    format: &str,
    recipe: [f64; 8],
    supersampling: [usize; 3],
    rotation: Option<[[f64; 3]; 3]>,
    matte: bool,
) -> PyResult<(Bound<'py, PyBytes>, [f64; 5])> {
    let result = py
        .detach(|| {
            job::render(
                vertices,
                faces,
                &RenderOptions {
                    width,
                    height,
                    chunk,
                    format,
                    rotation,
                    matte,
                    profile: Profile {
                        margin: recipe[0],
                        azimuth: recipe[1],
                        elevation: recipe[2],
                        flat_tilt: recipe[3],
                        flat_ratio: recipe[4],
                        albedo: [recipe[5], recipe[6], recipe[7]],
                        supersampling,
                    },
                },
            )
        })
        .map_err(PyValueError::new_err)?;
    Ok((PyBytes::new(py, &result.image), result.seconds))
}

fn profile(recipe: [f64; 8]) -> Profile {
    Profile {
        margin: recipe[0],
        azimuth: recipe[1],
        elevation: recipe[2],
        flat_tilt: recipe[3],
        flat_ratio: recipe[4],
        albedo: [recipe[5], recipe[6], recipe[7]],
        supersampling: [640, 2, 1],
    }
}
type RecoveryResult<'py> = (
    Bound<'py, PyBytes>,
    u64,
    usize,
    [f64; 3],
    [f64; 3],
    u64,
    bool,
    usize,
    Vec<f64>,
);

#[pyfunction]
pub fn render_stl_fallback<'py>(
    py: Python<'py>,
    path: std::path::PathBuf,
    width: usize,
    height: usize,
    budget: usize,
    recipe: [f64; 8],
) -> PyResult<RecoveryResult<'py>> {
    let result = py
        .detach(|| {
            printstash_render_core::fallback::render(&path, width, height, budget, profile(recipe))
        })
        .map_err(PyValueError::new_err)?;
    Ok((
        PyBytes::new(py, &result.image),
        result.sample.count,
        result.sample.triangles.len(),
        result.sample.lower,
        result.sample.upper,
        result.sample.scanned,
        result.sample.complete,
        result.candidates,
        result.seconds.to_vec(),
    ))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn render_stl_streaming<'py>(
    py: Python<'py>,
    path: std::path::PathBuf,
    width: usize,
    height: usize,
    max_triangles: usize,
    max_source_bytes: u64,
    max_candidates: usize,
    chunk: usize,
    timeout: f64,
    recipe: [f64; 8],
    max_lines: usize,
    max_line_bytes: usize,
) -> PyResult<RecoveryResult<'py>> {
    let result = py
        .detach(|| {
            printstash_render_core::streaming_job::render(
                path,
                width,
                height,
                printstash_render_core::streaming_job::Limits {
                    triangles: max_triangles,
                    source_bytes: max_source_bytes,
                    candidates: max_candidates,
                    chunk,
                    timeout,
                    max_lines,
                    max_line_bytes,
                },
                profile(recipe),
            )
        })
        .map_err(PyValueError::new_err)?;
    Ok((
        PyBytes::new(py, &result.image),
        result.count as u64,
        result.count,
        result.lower,
        result.upper,
        result.scanned,
        true,
        result.candidates,
        result.seconds.to_vec(),
    ))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn render_views<'py>(
    py: Python<'py>,
    vertices: &[u8],
    faces: &[u8],
    width: usize,
    height: usize,
    chunk: usize,
    recipe: [f64; 8],
    supersampling: [usize; 3],
    rotations: Vec<Option<[[f64; 3]; 3]>>,
) -> PyResult<Vec<Bound<'py, PyBytes>>> {
    let mut profile = profile(recipe);
    profile.supersampling = supersampling;
    let images = py
        .detach(|| job::render_views(vertices, faces, width, height, chunk, profile, &rotations))
        .map_err(PyValueError::new_err)?;
    Ok(images.iter().map(|bytes| PyBytes::new(py, bytes)).collect())
}
