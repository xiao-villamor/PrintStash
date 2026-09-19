//! Angle-weighted smooth normals with bounded per-position scratch storage.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn subtract(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
}

fn length(a: [f32; 3]) -> f32 {
    ((a[0] * a[0] + a[1] * a[1]) + a[2] * a[2]).sqrt()
}

#[pyfunction]
pub fn smooth_normals<'py>(
    py: Python<'py>,
    vertices: &[u8],
    faces: &[u8],
    positions: &[u8],
    position_count: usize,
    chunk_size: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let count = vertices.len() / 12;
    if !vertices.len().is_multiple_of(12)
        || !faces.len().is_multiple_of(24)
        || positions.len() / 8 != count
        || !positions.len().is_multiple_of(8)
        || position_count == 0
        || position_count > count
        || count > 6_000_000
        || faces.len() / 24 > 2_000_000
        || chunk_size == 0
    {
        return Err(PyValueError::new_err("invalid normal preparation buffers"));
    }
    let values = py
        .detach(|| -> Result<Vec<[f64; 3]>, &'static str> {
            let read_index = |bytes: &[u8]| u64::from_ne_bytes(bytes[..8].try_into().unwrap());
            if vertices
                .as_chunks::<4>()
                .0
                .iter()
                .any(|v| !f32::from_ne_bytes(*v).is_finite())
                || faces
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .any(|v| read_index(v) >= count as u64)
                || positions
                    .as_chunks::<8>()
                    .0
                    .iter()
                    .any(|v| read_index(v) >= position_count as u64)
            {
                return Err("invalid mesh values or indices");
            }
            let mut accum = vec![[0.0_f64; 3]; position_count];
            let mut batch = vec![[0.0_f64; 3]; position_count];
            let chunks = chunk_size.min(2_000_000) * 24;
            for chunk in faces.chunks(chunks) {
                batch.fill([0.0; 3]);
                for face in chunk.as_chunks::<24>().0 {
                    let ids: [usize; 3] =
                        std::array::from_fn(|i| read_index(&face[i * 8..]) as usize);
                    let points: [[f32; 3]; 3] = std::array::from_fn(|i| {
                        std::array::from_fn(|axis| {
                            f32::from_ne_bytes(
                                vertices[(ids[i] * 3 + axis) * 4..][..4].try_into().unwrap(),
                            )
                        })
                    });
                    let a = subtract(points[1], points[0]);
                    let b = subtract(points[2], points[0]);
                    let mut normal = [
                        a[1] * b[2] - a[2] * b[1],
                        a[2] * b[0] - a[0] * b[2],
                        a[0] * b[1] - a[1] * b[0],
                    ];
                    let norm = length(normal);
                    let divisor = if norm == 0.0 { 1.0 } else { norm };
                    normal.iter_mut().for_each(|v| *v /= divisor);
                    for corner in 0..3 {
                        let mut next = subtract(points[(corner + 1) % 3], points[corner]);
                        let mut prev = subtract(points[(corner + 2) % 3], points[corner]);
                        let nl = length(next).max(1e-20);
                        let pl = length(prev).max(1e-20);
                        next.iter_mut().for_each(|v| *v /= nl);
                        prev.iter_mut().for_each(|v| *v /= pl);
                        let dot = (next[0] * prev[0] + next[1] * prev[1]) + next[2] * prev[2];
                        let angle = dot.clamp(-1.0, 1.0).acos();
                        let position = read_index(&positions[ids[corner] * 8..]) as usize;
                        for axis in 0..3 {
                            batch[position][axis] += (normal[axis] * angle) as f64;
                        }
                    }
                }
                for (sum, chunk) in accum.iter_mut().zip(&batch) {
                    for axis in 0..3 {
                        sum[axis] += chunk[axis];
                    }
                }
            }
            for n in &mut accum {
                let norm = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
                if !norm.is_finite() {
                    return Err("normal arithmetic overflow");
                }
                if norm != 0.0 {
                    n.iter_mut().for_each(|v| *v /= norm);
                }
            }
            Ok(accum)
        })
        .map_err(PyValueError::new_err)?;
    PyBytes::new_with(py, position_count * 24, |output| {
        py.detach(|| {
            for (target, source) in output
                .as_chunks_mut::<8>()
                .0
                .iter_mut()
                .zip(values.iter().flatten())
            {
                target.copy_from_slice(&source.to_ne_bytes());
            }
        });
        Ok(())
    })
}
