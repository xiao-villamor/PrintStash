//! Bounded recovery reads: stratified binary records or a capped ASCII prefix.
use std::{
    fs::File,
    io::{BufRead, BufReader, Read},
    path::Path,
};

pub struct Sample {
    pub triangles: Vec<[[f32; 3]; 3]>,
    pub count: u64,
    pub lower: [f64; 3],
    pub upper: [f64; 3],
    pub scanned: u64,
    pub complete: bool,
}
impl Sample {
    fn new(capacity: usize) -> Self {
        Self {
            triangles: Vec::with_capacity(capacity),
            count: 0,
            lower: [f64::INFINITY; 3],
            upper: [f64::NEG_INFINITY; 3],
            scanned: 0,
            complete: false,
        }
    }
    fn push(&mut self, tri: [[f32; 3]; 3]) {
        for p in tri {
            for (a, value) in p.iter().enumerate() {
                self.lower[a] = self.lower[a].min(*value as f64);
                self.upper[a] = self.upper[a].max(*value as f64);
            }
        }
        self.triangles.push(tri);
    }
}

pub fn read(path: &Path, budget: usize) -> Result<Sample, String> {
    if !(1..=100_000).contains(&budget) {
        return Err("invalid sample budget".into());
    }
    let file = File::open(path).map_err(|e| e.to_string())?;
    let length = file.metadata().map_err(|e| e.to_string())?.len();
    let mut reader = BufReader::with_capacity(64 * 1024, file);
    let mut header = [0u8; 84];
    let binary = reader
        .read_exact(&mut header)
        .is_ok()
        .then(|| u32::from_le_bytes(header[80..].try_into().unwrap()) as u64)
        .filter(|n| *n > 0 && 84 + n * 50 <= length);
    if let Some(count) = binary {
        let samples = count.min(budget as u64);
        let mut result = Sample::new(samples as usize);
        result.count = count;
        result.scanned = 84 + samples * 50;
        let mut position = 84;
        let mut raw = [0u8; 50];
        for sample in 0..samples {
            let index = if sample == 0 {
                0
            } else if sample == samples - 1 {
                count - 1
            } else {
                (sample * count + count / 2) / samples
            };
            let target = 84 + index * 50;
            reader
                .seek_relative((target - position) as i64)
                .map_err(|e| e.to_string())?;
            if reader.read_exact(&mut raw).is_err() {
                break;
            }
            position = target + 50;
            let tri: [[f32; 3]; 3] = std::array::from_fn(|v| {
                std::array::from_fn(|a| {
                    let offset = 12 + (v * 3 + a) * 4;
                    f32::from_le_bytes(raw[offset..offset + 4].try_into().unwrap())
                })
            });
            if tri.iter().flatten().all(|v| v.is_finite()) {
                result.push(tri);
            }
        }
        result.complete = result.triangles.len() as u64 == count;
        if result.triangles.is_empty() {
            return Err("no valid STL facets".into());
        }
        return Ok(result);
    }
    // A recovery reader deliberately accepts vertex records from damaged ASCII
    // sources, but does not claim complete geometry after invalid input or limits.
    let mut reader = BufReader::new(File::open(path).map_err(|e| e.to_string())?);
    let mut result = Sample::new(budget);
    result.scanned = length.min(84);
    let mut vertices = Vec::<[f32; 3]>::with_capacity(3);
    let mut lines = 0;
    let mut valid = true;
    let mut draining = false;
    let mut raw = Vec::with_capacity(65537);
    while result.scanned < 16 * 1024 * 1024 && lines < 1_000_000 && result.triangles.len() < budget
    {
        raw.clear();
        let limit = (16 * 1024 * 1024 - result.scanned).min(65537);
        (&mut reader)
            .take(limit)
            .read_until(b'\n', &mut raw)
            .map_err(|e| e.to_string())?;
        if raw.is_empty() {
            result.complete = valid && vertices.is_empty() && !draining;
            break;
        }
        result.scanned += raw.len() as u64;
        let ends = matches!(raw.last(), Some(b'\n' | b'\r'));
        if draining {
            draining = !ends;
            continue;
        }
        lines += 1;
        if raw.len() > 65536 {
            vertices.clear();
            valid = false;
            draining = !ends;
            continue;
        }
        let text: String = raw
            .iter()
            .filter(|v| v.is_ascii())
            .map(|v| *v as char)
            .collect();
        let mut parts = text.split_ascii_whitespace();
        if !parts
            .next()
            .is_some_and(|p| p.eq_ignore_ascii_case("vertex"))
        {
            continue;
        }
        let values: Vec<_> = parts.collect();
        if values.len() != 3 {
            continue;
        }
        let point: Option<Vec<f32>> = values
            .iter()
            .map(|v| {
                v.parse::<f64>()
                    .ok()
                    .filter(|v| v.is_finite() && v.abs() <= f32::MAX as f64)
                    .map(|v| v as f32)
            })
            .collect();
        match point {
            None => {
                valid = false;
                vertices.clear();
            }
            Some(point) => {
                vertices.push([point[0], point[1], point[2]]);
                if vertices.len() == 3 {
                    result.push([vertices[0], vertices[1], vertices[2]]);
                    vertices.clear();
                }
            }
        }
    }
    result.count = result.triangles.len() as u64;
    if result.count == 0 {
        return Err("no valid STL facets".into());
    }
    Ok(result)
}
