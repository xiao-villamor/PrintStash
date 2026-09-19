//! Edge-connected face labels using compact indices and petgraph union-find.
use petgraph::unionfind::UnionFind;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

#[pyfunction]
pub fn component_labels<'py>(
    py: Python<'py>,
    faces: &[u8],
    vertex_count: usize,
) -> PyResult<Bound<'py, PyBytes>> {
    let count = faces.len() / 24;
    if !faces.len().is_multiple_of(24) || count > 2_000_000 || vertex_count > 6_000_000 {
        return Err(PyValueError::new_err("invalid component buffers"));
    }
    let labels = py
        .detach(|| -> Result<Vec<u32>, &'static str> {
            let mut edges = Vec::with_capacity(count * 3);
            for (face, bytes) in faces.as_chunks::<24>().0.iter().enumerate() {
                let ids: [u64; 3] = std::array::from_fn(|i| {
                    u64::from_ne_bytes(bytes[i * 8..i * 8 + 8].try_into().unwrap())
                });
                if ids.iter().any(|&v| v >= vertex_count as u64) {
                    return Err("invalid component vertex index");
                }
                for corner in 0..3 {
                    let a = ids[corner] as u32;
                    let b = ids[(corner + 1) % 3] as u32;
                    edges.push(([a.min(b), a.max(b)], face as u32));
                }
            }
            edges.sort_unstable();
            let mut sets = UnionFind::<u32>::new(count);
            for pair in edges.windows(2) {
                if pair[0].0 == pair[1].0 {
                    sets.union(pair[0].1, pair[1].1);
                }
            }
            drop(edges);
            let roots = sets.into_labeling();
            let mut minima = vec![u32::MAX; count];
            for (face, &root) in roots.iter().enumerate() {
                minima[root as usize] = minima[root as usize].min(face as u32);
            }
            Ok(roots
                .into_iter()
                .map(|root| minima[root as usize])
                .collect())
        })
        .map_err(PyValueError::new_err)?;
    PyBytes::new_with(py, count * 8, |bytes| {
        py.detach(|| {
            for (out, label) in bytes.as_chunks_mut::<8>().0.iter_mut().zip(labels) {
                out.copy_from_slice(&(label as u64).to_ne_bytes());
            }
        });
        Ok(())
    })
}
