//! Python transport for frames owned entirely by the standalone Rust renderer.
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyclass]
pub struct NativeFrame {
    inner: printstash_render_core::Frame,
}

#[pymethods]
impl NativeFrame {
    #[new]
    pub(crate) fn new(width: usize, height: usize) -> PyResult<Self> {
        Ok(Self {
            inner: printstash_render_core::Frame::new(width, height)
                .map_err(PyValueError::new_err)?,
        })
    }

    pub(crate) fn draw_phong(
        &mut self,
        py: Python<'_>,
        triangles: &[u8],
        itemsize: usize,
        normals: &[u8],
        normal_itemsize: usize,
        lighting: [f64; 31],
    ) -> PyResult<usize> {
        self.inner.check().map_err(PyRuntimeError::new_err)?;
        py.detach(|| {
            self.inner
                .draw_phong(triangles, itemsize, normals, normal_itemsize, lighting)
        })
        .map_err(PyValueError::new_err)
    }

    pub(crate) fn draw_flat(
        &mut self,
        py: Python<'_>,
        triangles: &[u8],
        itemsize: usize,
        color: [u8; 3],
    ) -> PyResult<usize> {
        self.inner.check().map_err(PyRuntimeError::new_err)?;
        py.detach(|| self.inner.draw_flat(triangles, itemsize, color))
            .map_err(PyValueError::new_err)
    }

    pub(crate) fn rgba<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        self.inner.check().map_err(PyRuntimeError::new_err)?;
        PyBytes::new_with(py, self.inner.output_bytes(), |output| {
            py.detach(|| self.inner.write_rgba(output))
                .map_err(PyValueError::new_err)
        })
    }
}
