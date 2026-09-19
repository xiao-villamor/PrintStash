//! Python transport for the standalone Rust image engine.
use printstash_render_core::images;
use pyo3::{exceptions::PyValueError, prelude::*, types::PyBytes};

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn process_image<'py>(
    py: Python<'py>,
    rgba: &[u8],
    source_width: u32,
    source_height: u32,
    width: u32,
    height: u32,
    filter: &str,
    alpha: bool,
    vignette: bool,
    format: &str,
) -> PyResult<Bound<'py, PyBytes>> {
    let output = py
        .detach(|| {
            images::process_image(
                rgba,
                source_width,
                source_height,
                width,
                height,
                filter,
                alpha,
                vignette,
                format,
            )
        })
        .map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
pub fn shade_depth<'py>(
    py: Python<'py>,
    depth: &[u8],
    width: u32,
    height: u32,
    scale: f64,
    albedo: [f32; 3],
) -> PyResult<Bound<'py, PyBytes>> {
    let output = py
        .detach(|| images::shade_depth(depth, width, height, scale, albedo))
        .map_err(PyValueError::new_err)?;
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
pub fn normalize_thumbnail<'py>(
    py: Python<'py>,
    data: &[u8],
    width: u32,
    normalize: bool,
    margin: f64,
) -> PyResult<Option<Bound<'py, PyBytes>>> {
    let output = py
        .detach(|| images::normalize_thumbnail(data, width, normalize, margin))
        .map_err(PyValueError::new_err)?;
    Ok(output.map(|bytes| PyBytes::new(py, &bytes)))
}
