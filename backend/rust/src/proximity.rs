//! PyO3 translation boundary for the framework-free proximity tree.
use printstash_similarity_core::SurfaceTree as CoreSurfaceTree;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::similarity::{points, triangles};

type PyAlignment = ([[f64; 3]; 3], [f64; 3], f64, f64);

#[pyclass(frozen)]
pub struct SurfaceTree {
    inner: CoreSurfaceTree,
}

#[pymethods]
impl SurfaceTree {
    #[new]
    fn new(py: Python<'_>, triangle_bytes: &[u8]) -> PyResult<Self> {
        let triangles = triangles(triangle_bytes)?;
        let inner = py
            .detach(|| CoreSurfaceTree::try_new(triangles))
            .map_err(|error| PyValueError::new_err(error.code()))?;
        Ok(Self { inner })
    }

    fn closest<'py>(
        &self,
        py: Python<'py>,
        point_bytes: &[u8],
        max_work: usize,
    ) -> PyResult<Bound<'py, PyBytes>> {
        let points =
            points(point_bytes).map_err(|_| PyValueError::new_err("invalid_proximity_points"))?;
        let closest = py
            .detach(|| self.inner.closest(&points, max_work))
            .map_err(|error| PyValueError::new_err(error.code()))?;
        let mut output = Vec::with_capacity(closest.len() * 32);
        for closest in closest {
            for value in [
                closest.distance,
                closest.point[0],
                closest.point[1],
                closest.point[2],
            ] {
                output.extend_from_slice(&value.to_ne_bytes());
            }
        }
        Ok(PyBytes::new(py, &output))
    }

    fn align(
        &self,
        py: Python<'_>,
        point_bytes: &[u8],
        rotation: [f64; 9],
        diagonal: f64,
    ) -> PyResult<PyAlignment> {
        let points =
            points(point_bytes).map_err(|_| PyValueError::new_err("invalid_proximity_points"))?;
        let result = py
            .detach(|| self.inner.align(&points, rotation, diagonal))
            .map_err(|error| PyValueError::new_err(error.code()))?;
        Ok((
            result.rotation,
            result.translation,
            result.convergence,
            result.error,
        ))
    }
}
