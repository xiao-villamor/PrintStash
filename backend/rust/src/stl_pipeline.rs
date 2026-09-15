//! Bounded binary STL passes with no Python callbacks and one owned depth buffer.
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::fs::{File, Metadata};
use std::io::{BufReader, Read};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const RESERVOIR: usize = 4096;
const MAX_SOURCE: u64 = 1 << 30;
type Bounds = [f64; 3];
type Summary = (usize, u64, Bounds, Bounds, Vec<[f32; 3]>);
type Sample<'py> = (Bound<'py, PyBytes>, u64, usize, Bounds, Bounds, u64, bool);

fn io_error(error: std::io::Error) -> String {
    error.to_string()
}
fn identity_matches(a: &Metadata, b: &Metadata) -> bool {
    if a.len() != b.len() || a.modified().ok() != b.modified().ok() {
        return false;
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        if a.dev() != b.dev() || a.ino() != b.ino() {
            return false;
        }
    }
    true
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
    path: PathBuf,
    identity: Metadata,
    count: usize,
    chunk: usize,
    deadline: Instant,
    summary: Option<Summary>,
    failed: bool,
}
impl NativeStlSource {
    fn check(&self) -> Result<(), String> {
        if self.failed {
            return Err("STL source has failed".into());
        }
        if Instant::now() >= self.deadline {
            return Err("deadline".into());
        }
        let now = std::fs::metadata(&self.path).map_err(io_error)?;
        if !identity_matches(&self.identity, &now) {
            return Err("source changed".into());
        }
        Ok(())
    }
    fn pass(
        &self,
        mut consume: impl FnMut(&[[[f32; 3]; 3]]) -> Result<(), String>,
    ) -> Result<(Bounds, Bounds), String> {
        self.check()?;
        let file = File::open(&self.path).map_err(io_error)?;
        if !identity_matches(&self.identity, &file.metadata().map_err(io_error)?) {
            return Err("source changed".into());
        }
        let mut reader = BufReader::with_capacity(64 * 1024, file);
        if binary_header(&mut reader, true)? != self.count as u64 {
            return Err("source changed".into());
        }
        let mut raw = vec![0u8; self.chunk * 50];
        let mut triangles = Vec::with_capacity(self.chunk);
        let mut lower = [f64::INFINITY; 3];
        let mut upper = [f64::NEG_INFINITY; 3];
        let mut parsed = 0;
        while parsed < self.count {
            if Instant::now() >= self.deadline {
                return Err("deadline".into());
            }
            let count = self.chunk.min(self.count - parsed);
            reader
                .read_exact(&mut raw[..count * 50])
                .map_err(io_error)?;
            triangles.clear();
            for record in raw[..count * 50].chunks_exact(50) {
                let tri = points(record);
                for point in tri {
                    for axis in 0..3 {
                        let value = point[axis] as f64;
                        if !value.is_finite() {
                            return Err("non-finite coordinate".into());
                        }
                        lower[axis] = lower[axis].min(value);
                        upper[axis] = upper[axis].max(value);
                    }
                }
                triangles.push(tri);
            }
            consume(&triangles)?;
            parsed += count;
        }
        self.check()?;
        Ok((lower, upper))
    }
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
        if !(1..=20_000_000).contains(&max_triangles)
            || !(1..=MAX_SOURCE).contains(&max_source_bytes)
            || !(1..=8192).contains(&chunk_triangles)
            || !timeout_seconds.is_finite()
            || !(0.0..=45.0).contains(&timeout_seconds)
            || timeout_seconds == 0.0
        {
            return Err(PyValueError::new_err("invalid STL limits"));
        }
        py.detach(|| {
            let file = File::open(&path).map_err(PyOSError::new_err)?;
            let identity = file.metadata().map_err(PyOSError::new_err)?;
            if identity.len() > max_source_bytes {
                return Err(PyValueError::new_err("source budget"));
            }
            let mut reader = BufReader::with_capacity(64 * 1024, file);
            let count = binary_header(&mut reader, true).map_err(PyValueError::new_err)? as usize;
            if count > max_triangles {
                return Err(PyValueError::new_err("triangle budget"));
            }
            Ok(Self {
                path,
                identity,
                count,
                chunk: chunk_triangles,
                deadline: Instant::now() + Duration::from_secs_f64(timeout_seconds),
                summary: None,
                failed: false,
            })
        })
    }
    fn analyze(&mut self, py: Python<'_>) -> PyResult<Summary> {
        let result = py.detach(|| -> Result<Summary, String> {
            self.check()?;
            if let Some(summary) = &self.summary {
                return Ok(summary.clone());
            }
            let mut seen = 0usize;
            let mut state = 0x9e3779b9u32;
            let mut reservoir = Vec::with_capacity(RESERVOIR);
            let (lower, upper) = self.pass(|triangles| {
                for tri in triangles {
                    seen += 1;
                    let index = if reservoir.len() < RESERVOIR {
                        Some(reservoir.len())
                    } else {
                        state = state.wrapping_mul(1664525).wrapping_add(1013904223);
                        let index = state as usize % seen;
                        (index < RESERVOIR).then_some(index)
                    };
                    if let Some(index) = index {
                        let center = std::array::from_fn(|axis| {
                            ((tri[0][axis] + tri[1][axis]) + tri[2][axis]) / 3.0
                        });
                        if index == reservoir.len() {
                            reservoir.push(center);
                        } else {
                            reservoir[index] = center;
                        }
                    }
                }
                Ok(())
            })?;
            Ok((self.count, self.identity.len(), lower, upper, reservoir))
        });
        match result {
            Ok(summary) => {
                self.summary = Some(summary.clone());
                Ok(summary)
            }
            Err(error) => {
                self.failed = true;
                Err(PyValueError::new_err(error))
            }
        }
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
        let result = py.detach(|| -> Result<_, String> {
            let first = self
                .summary
                .as_ref()
                .ok_or("source has not been analyzed")?;
            let mut depth = vec![f64::INFINITY; width * height];
            let mut packed = Vec::with_capacity(self.chunk * 36);
            let mut used = 0;
            let (second_min, second_max) = self.pass(|triangles| {
                packed.clear();
                for tri in triangles {
                    if tri.iter().any(|p| {
                        (0..3).any(|a| (p[a] as f64) < lower[a] || (p[a] as f64) > upper[a])
                    }) {
                        continue;
                    }
                    let view: [[f32; 3]; 3] = std::array::from_fn(|i| {
                        let v: [f32; 3] = std::array::from_fn(|a| tri[i][a] - center[a]);
                        std::array::from_fn(|a| {
                            (v[0] * rotation[a][0] + v[1] * rotation[a][1]) + v[2] * rotation[a][2]
                        })
                    });
                    // Preserve the worker's existing degeneracy contract.
                    let a: [f32; 3] = std::array::from_fn(|i| view[i][1] - view[i][0]);
                    let b: [f32; 3] = std::array::from_fn(|i| view[i][2] - view[i][0]);
                    let n = [
                        a[1] * b[2] - a[2] * b[1],
                        a[2] * b[0] - a[0] * b[2],
                        a[0] * b[1] - a[1] * b[0],
                    ];
                    let norm = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
                    if !norm.is_finite() || norm <= 1e-12 {
                        continue;
                    }
                    let screen: [[f32; 3]; 3] = view.map(|v| {
                        [
                            (v[0] - midpoint[0]) * scale + width as f32 * 0.5,
                            height as f32 * 0.5 - (v[1] - midpoint[1]) * scale,
                            v[2],
                        ]
                    });
                    if !screen.iter().flatten().all(|v| v.is_finite()) {
                        continue;
                    }
                    for value in screen.iter().flatten() {
                        packed.extend_from_slice(&value.to_ne_bytes());
                    }
                }
                used += crate::streaming_preview::draw_depth(
                    &packed,
                    &mut depth,
                    width,
                    height,
                    max_candidates - used,
                )?;
                if used >= max_candidates {
                    return Err("candidate budget".into());
                }
                Ok(())
            })?;
            if second_min != first.2 || second_max != first.3 {
                return Err("source changed between passes".into());
            }
            if !depth.iter().any(|v| v.is_finite()) {
                return Err("no visible triangles".into());
            }
            Ok((depth, used))
        });
        let (depth, used) = result.map_err(|error| {
            self.failed = true;
            PyValueError::new_err(error)
        })?;
        let bytes = PyBytes::new_with(py, depth.len() * 4, |output| {
            py.detach(|| {
                for (target, value) in output.chunks_exact_mut(4).zip(depth) {
                    target.copy_from_slice(&(value as f32).to_ne_bytes());
                }
            });
            Ok(())
        })?;
        Ok((bytes, used))
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
