//! Bounded binary STL passes with no Python callbacks and one owned depth buffer.
use std::fs::{File, Metadata};
use std::io::{BufReader, Read};
use std::path::PathBuf;
use std::time::{Duration, Instant};

const RESERVOIR: usize = 4096;
const MAX_SOURCE: u64 = 1 << 30;
type Bounds = [f64; 3];
pub type Summary = (usize, u64, Bounds, Bounds, Vec<[f32; 3]>);

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

pub struct NativeStlSource {
    path: PathBuf,
    identity: Metadata,
    count: usize,
    binary: bool,
    max_triangles: usize,
    max_lines: usize,
    max_line_bytes: usize,
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
    ) -> Result<(usize, Bounds, Bounds), String> {
        self.check()?;
        let file = File::open(&self.path).map_err(io_error)?;
        if !identity_matches(&self.identity, &file.metadata().map_err(io_error)?) {
            return Err("source changed".into());
        }
        let mut reader = BufReader::with_capacity(64 * 1024, file);
        if !self.binary {
            let result = crate::stl_ascii::read(
                reader,
                crate::stl_ascii::Limits {
                    triangles: self.max_triangles,
                    source_bytes: self.identity.len(),
                    lines: self.max_lines,
                    line_bytes: self.max_line_bytes,
                    deadline: self.deadline,
                    chunk: self.chunk,
                },
                consume,
            )?;
            self.check()?;
            return Ok(result);
        }
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
            for record in raw[..count * 50].as_chunks::<50>().0 {
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
        Ok((self.count, lower, upper))
    }
}
impl NativeStlSource {
    pub fn new(
        path: PathBuf,
        max_triangles: usize,
        max_source_bytes: u64,
        chunk_triangles: usize,
        timeout_seconds: f64,
    ) -> Result<Self, String> {
        if !(1..=20_000_000).contains(&max_triangles)
            || !(1..=MAX_SOURCE).contains(&max_source_bytes)
            || !(1..=8192).contains(&chunk_triangles)
            || !timeout_seconds.is_finite()
            || !(0.0..=45.0).contains(&timeout_seconds)
            || timeout_seconds == 0.0
        {
            return Err("invalid STL limits".into());
        }
        (|| {
            let file = File::open(&path).map_err(io_error)?;
            let identity = file.metadata().map_err(io_error)?;
            if identity.len() > max_source_bytes {
                return Err("source budget".into());
            }
            let mut reader = BufReader::with_capacity(64 * 1024, file);
            let count = binary_header(&mut reader, true).ok().map(|v| v as usize);
            let binary = count.is_some();
            let count = count.unwrap_or(0);
            if count > max_triangles {
                return Err("triangle budget".into());
            }
            Ok(Self {
                path,
                identity,
                count,
                binary,
                max_triangles,
                max_lines: 10_000_000,
                max_line_bytes: 65536,
                chunk: chunk_triangles,
                deadline: Instant::now() + Duration::from_secs_f64(timeout_seconds),
                summary: None,
                failed: false,
            })
        })()
    }
    pub fn set_ascii_limits(&mut self, lines: usize, bytes: usize) -> Result<(), String> {
        if !(1..=10_000_000).contains(&lines) || !(1..=65536).contains(&bytes) {
            return Err("invalid ASCII limits".into());
        }
        self.max_lines = lines;
        self.max_line_bytes = bytes;
        Ok(())
    }
    pub fn analyze(&mut self) -> Result<Summary, String> {
        let result = (|| -> Result<Summary, String> {
            self.check()?;
            if let Some(summary) = &self.summary {
                return Ok(summary.clone());
            }
            let mut seen = 0usize;
            let mut state = 0x9e3779b9u32;
            let mut reservoir = Vec::with_capacity(RESERVOIR);
            let (count, lower, upper) = self.pass(|triangles| {
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
            Ok((count, self.identity.len(), lower, upper, reservoir))
        })();
        match result {
            Ok(summary) => {
                self.count = summary.0;
                self.summary = Some(summary.clone());
                Ok(summary)
            }
            Err(error) => {
                self.failed = true;
                Err(error)
            }
        }
    }
    #[allow(clippy::too_many_arguments)]
    pub fn render_depth(
        &mut self,
        width: usize,
        height: usize,
        max_candidates: usize,
        center: [f32; 3],
        rotation: [[f32; 3]; 3],
        lower: Bounds,
        upper: Bounds,
        midpoint: [f32; 3],
        scale: f32,
    ) -> Result<(Vec<u8>, usize), String> {
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
            return Err("invalid projection".into());
        }
        let result = (|| -> Result<_, String> {
            let first = self
                .summary
                .as_ref()
                .ok_or("source has not been analyzed")?;
            let mut depth = vec![f64::INFINITY; width * height];
            let mut packed = Vec::with_capacity(self.chunk * 36);
            let mut used = 0;
            let (second_count, second_min, second_max) = self.pass(|triangles| {
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
            if second_count != first.0 || second_min != first.2 || second_max != first.3 {
                return Err("source changed between passes".into());
            }
            if !depth.iter().any(|v| v.is_finite()) {
                return Err("no visible triangles".into());
            }
            Ok((depth, used))
        })();
        let (depth, used) = result.inspect_err(|_| {
            self.failed = true;
        })?;
        let bytes = depth
            .into_iter()
            .flat_map(|v| (v as f32).to_ne_bytes())
            .collect();
        Ok((bytes, used))
    }
}
