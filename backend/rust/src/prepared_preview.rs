//! Python transport for the standalone Rust mesh renderer.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyclass(module = "printstash_mesh_native")]
pub struct PreparedPreview {
    inner: printstash_render_core::PreparedPreview,
    #[pyo3(get)]
    lower: [f32; 3],
    #[pyo3(get)]
    upper: [f32; 3],
}

#[pymethods]
impl PreparedPreview {
    #[new]
    fn new(py: Python<'_>, vertices: &[u8], faces: &[u8], chunk: usize) -> PyResult<Self> {
        let inner = py
            .detach(|| printstash_render_core::PreparedPreview::new(vertices, faces, chunk))
            .map_err(PyValueError::new_err)?;
        Ok(Self {
            lower: inner.lower,
            upper: inner.upper,
            inner,
        })
    }

    #[allow(clippy::too_many_arguments)]
    fn render<'py>(
        &self,
        py: Python<'py>,
        rotation: [[f64; 3]; 3],
        handedness: f64,
        width: usize,
        height: usize,
        margin: f64,
        lighting: [f64; 31],
        flat_color: [u8; 3],
    ) -> PyResult<Bound<'py, PyBytes>> {
        let result = py
            .detach(|| {
                self.inner.render_frame(
                    rotation, handedness, width, height, margin, lighting, flat_color,
                )
            })
            .map_err(PyValueError::new_err)?;
        PyBytes::new_with(py, result.output_bytes(), |output| {
            py.detach(|| result.write_rgba(output))
                .map_err(PyValueError::new_err)
        })
    }
}
