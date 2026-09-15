//! Measure only the bounds and signed volume needed during import.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

type Bounds = Option<[[f64; 3]; 2]>;

fn measure(triangles: &[u8]) -> Result<(Bounds, f64), &'static str> {
    if !triangles.len().is_multiple_of(72) {
        return Err("invalid triangle buffer length");
    }
    let mut low = [f64::INFINITY; 3];
    let mut high = [f64::NEG_INFINITY; 3];
    let mut sum = 0.0_f64;
    let mut correction = 0.0_f64;
    for bytes in triangles.chunks_exact(72) {
        let mut v = [0.0_f64; 9];
        for (target, source) in v.iter_mut().zip(bytes.chunks_exact(8)) {
            *target = f64::from_ne_bytes(source.try_into().unwrap());
            if !target.is_finite() {
                return Err("triangle coordinates must be finite");
            }
        }
        for vertex in v.chunks_exact(3) {
            for axis in 0..3 {
                low[axis] = low[axis].min(vertex[axis]);
                high[axis] = high[axis].max(vertex[axis]);
            }
        }
        // Match the existing surface integral, including its convention for
        // open surfaces: cross((b-a), (c-b)).x * (a.x+b.x+c.x) / 6.
        // No inertia tensor, triangle array or per-face cross array is needed.
        let cross_x = (v[4] - v[1]) * (v[8] - v[5]) - (v[5] - v[2]) * (v[7] - v[4]);
        let term = cross_x * ((v[0] + v[3]) + v[6]);
        let next = sum + term;
        correction += if sum.abs() >= term.abs() {
            (sum - next) + term
        } else {
            (term - next) + sum
        };
        sum = next;
    }
    Ok((
        (!triangles.is_empty()).then_some([low, high]),
        sum + correction,
    ))
}

#[pyfunction]
pub fn measure_triangles(py: Python<'_>, triangles: &[u8]) -> PyResult<(Bounds, f64)> {
    py.detach(|| measure(triangles))
        .map_err(PyValueError::new_err)
}
