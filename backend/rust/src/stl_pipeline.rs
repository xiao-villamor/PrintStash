//! Bounded binary STL passes with no Python callbacks and one owned depth buffer.
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};

type Bounds = [f64; 3];
type Sample<'py> = (Bound<'py, PyBytes>, u64, usize, Bounds, Bounds, u64, bool);

fn io_error(error: std::io::Error) -> String {
    error.to_string()
}
fn points(record: &[u8]) -> [[f32; 3]; 3] {
    std::array::from_fn(|vertex| {
        std::array::from_fn(|axis| {
            let offset = 12 + (vertex * 3 + axis) * 4;
            f32::from_le_bytes(record[offset..offset + 4].try_into().unwrap())
        })
    })
}
fn binary_header(reader: &mut BufReader<File>, exact: bool) -> Result<u64, String> {
    let length = reader.get_ref().metadata().map_err(io_error)?.len();
    let mut header = [0u8; 84];
    reader.read_exact(&mut header).map_err(io_error)?;
    let count = u32::from_le_bytes(header[80..].try_into().unwrap()) as u64;
    let expected = 84 + count * 50;
    if count == 0 || length < expected || (exact && length != expected) {
        return Err("not an exact binary STL".into());
    }
    Ok(count)
}

#[pyclass(module = "printstash_mesh_native")]
pub struct NativeStlSource {
    inner: printstash_render_core::stl_source::NativeStlSource,
}
#[pymethods]
impl NativeStlSource {
    #[new]
    fn new(
        py: Python<'_>,
        path: PathBuf,
        max_triangles: usize,
        max_source_bytes: u64,
        chunk_triangles: usize,
        timeout_seconds: f64,
    ) -> PyResult<Self> {
        std::fs::metadata(&path).map_err(PyOSError::new_err)?;
        let inner = py
            .detach(|| {
                printstash_render_core::stl_source::NativeStlSource::new(
                    path,
                    max_triangles,
                    max_source_bytes,
                    chunk_triangles,
                    timeout_seconds,
                )
            })
            .map_err(PyValueError::new_err)?;
        Ok(Self { inner })
    }
    fn analyze(&mut self, py: Python<'_>) -> PyResult<printstash_render_core::stl_source::Summary> {
        py.detach(|| self.inner.analyze())
            .map_err(PyValueError::new_err)
    }
    #[allow(clippy::too_many_arguments)]
    fn render_depth<'py>(
        &mut self,
        py: Python<'py>,
        width: usize,
        height: usize,
        max_candidates: usize,
        center: [f32; 3],
        rotation: [[f32; 3]; 3],
        lower: Bounds,
        upper: Bounds,
        midpoint: [f32; 3],
        scale: f32,
    ) -> PyResult<(Bound<'py, PyBytes>, usize)> {
        if !(1..=2048).contains(&width)
            || !(1..=2048).contains(&height)
            || !(1..=20_000_000).contains(&max_candidates)
            || !scale.is_finite()
            || scale <= 0.0
            || !center
                .iter()
                .chain(midpoint.iter())
                .chain(rotation.iter().flatten())
                .all(|v| v.is_finite())
            || !lower.iter().chain(upper.iter()).all(|v| v.is_finite())
            || (0..3).any(|a| lower[a] > upper[a])
        {
            return Err(PyValueError::new_err("invalid projection"));
        }
        let (depth, used) = py
            .detach(|| {
                self.inner.render_depth(
                    width,
                    height,
                    max_candidates,
                    center,
                    rotation,
                    lower,
                    upper,
                    midpoint,
                    scale,
                )
            })
            .map_err(PyValueError::new_err)?;
        Ok((PyBytes::new(py, &depth), used))
    }
}

#[pyfunction]
pub fn sample_binary_stl<'py>(
    py: Python<'py>,
    path: PathBuf,
    budget: usize,
) -> PyResult<Option<Sample<'py>>> {
    if !(1..=100_000).contains(&budget) {
        return Err(PyValueError::new_err("invalid sample budget"));
    }
    let result = py
        .detach(|| -> Result<_, std::io::Error> {
            let file = File::open(Path::new(&path))?;
            // Widely separated samples cannot reuse read-ahead. Read only the
            // selected record there; nearby samples share a larger buffer.
            let capacity = if file.metadata()?.len() / budget as u64 >= 64 * 1024 {
                50
            } else {
                64 * 1024
            };
            let mut reader = BufReader::with_capacity(capacity, file);
            let count = match binary_header(&mut reader, false) {
                Ok(count) => count,
                Err(_) => return Ok(None),
            };
            let samples = (budget as u64).min(count);
            let mut coordinates = Vec::with_capacity(samples as usize * 36);
            let mut lower = [f64::INFINITY; 3];
            let mut upper = [f64::NEG_INFINITY; 3];
            let mut parsed = 0;
            let mut raw = [0u8; 50];
            let mut position = 84u64;
            for sample in 0..samples {
                let index = if sample == 0 {
                    0
                } else if sample == samples - 1 {
                    count - 1
                } else {
                    (sample * count + count / 2) / samples
                };
                let target = 84 + index * 50;
                // Samples are ordered. Keep unread bytes when the next record
                // is already buffered instead of discarding it on every seek.
                reader.seek_relative((target - position) as i64)?;
                if reader.read_exact(&mut raw).is_err() {
                    break;
                }
                position = target + 50;
                let tri = points(&raw);
                if !tri.iter().flatten().all(|v| v.is_finite()) {
                    continue;
                }
                for point in tri {
                    for axis in 0..3 {
                        lower[axis] = lower[axis].min(point[axis] as f64);
                        upper[axis] = upper[axis].max(point[axis] as f64);
                        coordinates.extend_from_slice(&point[axis].to_ne_bytes());
                    }
                }
                parsed += 1;
            }
            Ok((parsed > 0).then_some((
                coordinates,
                count,
                parsed,
                lower,
                upper,
                84 + samples * 50,
                parsed as u64 == samples && samples == count,
            )))
        })
        .map_err(PyOSError::new_err)?;
    match result {
        None => Ok(None),
        Some((coordinates, count, parsed, lower, upper, scanned, complete)) => Ok(Some((
            PyBytes::new(py, &coordinates),
            count,
            parsed,
            lower,
            upper,
            scanned,
            complete,
        ))),
    }
}
