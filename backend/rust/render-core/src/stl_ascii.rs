//! Streaming validation for the slicer-compatible ASCII STL grammar.
//! Accept complete facets without endsolid, as the existing reader does.
use std::{
    fs::File,
    io::{BufRead, BufReader, Read},
    time::Instant,
};
pub(crate) struct Limits {
    pub triangles: usize,
    pub source_bytes: u64,
    pub lines: usize,
    pub line_bytes: usize,
    pub deadline: Instant,
    pub chunk: usize,
}
pub(crate) fn read(
    mut reader: BufReader<File>,
    limits: Limits,
    mut consume: impl FnMut(&[[[f32; 3]; 3]]) -> Result<(), String>,
) -> Result<(usize, [f64; 3], [f64; 3]), String> {
    let mut raw = Vec::with_capacity(limits.line_bytes.min(65536) + 1);
    let mut state = 0;
    let mut ended = false;
    let mut vertices = Vec::with_capacity(3);
    let mut batch = Vec::with_capacity(limits.chunk);
    let mut parsed = 0;
    let mut lower = [f64::INFINITY; 3];
    let mut upper = [f64::NEG_INFINITY; 3];
    let number = |s: &str| {
        s.parse::<f64>()
            .ok()
            .filter(|v| v.is_finite() && v.abs() <= f32::MAX as f64)
            .map(|v| v as f32)
            .ok_or_else(|| "non-finite or invalid coordinate".to_string())
    };
    let mut lines = 0;
    let mut scanned = 0u64;
    loop {
        if Instant::now() >= limits.deadline {
            return Err("deadline".into());
        }
        if lines >= limits.lines {
            return Err("line budget".into());
        }
        raw.clear();
        (&mut reader)
            .take(((limits.line_bytes + 1) as u64).min(limits.source_bytes - scanned + 1))
            .read_until(b'\n', &mut raw)
            .map_err(|e| e.to_string())?;
        if raw.is_empty() {
            break;
        }
        scanned += raw.len() as u64;
        if scanned > limits.source_bytes {
            return Err("source byte budget".into());
        }
        lines += 1;
        if raw.len() > limits.line_bytes {
            return Err("line too long".into());
        }
        if !raw.is_ascii() {
            return Err("non-ascii input".into());
        }
        let text = std::str::from_utf8(&raw).map_err(|e| e.to_string())?.trim();
        if text.is_empty() || text.starts_with('#') || text.starts_with("//") {
            continue;
        }
        let parts: Vec<_> = text.split_ascii_whitespace().collect();
        let keyword = parts[0].to_ascii_lowercase();
        match state {
            0 => {
                if ended {
                    return Err("content after endsolid".into());
                }
                match keyword.as_str() {
                    "solid" => {}
                    "endsolid" => ended = true,
                    "facet" if parts.len() == 5 && parts[1].eq_ignore_ascii_case("normal") => {
                        for part in &parts[2..] {
                            number(part)?;
                        }
                        state = 1;
                    }
                    _ => return Err("unexpected ASCII STL token".into()),
                }
            }
            1 if parts.len() == 2
                && keyword == "outer"
                && parts[1].eq_ignore_ascii_case("loop") =>
            {
                vertices.clear();
                state = 2;
            }
            2 if parts.len() == 4 && keyword == "vertex" => {
                vertices.push([number(parts[1])?, number(parts[2])?, number(parts[3])?]);
                if vertices.len() == 3 {
                    state = 3;
                }
            }
            3 if parts.len() == 1 && keyword == "endloop" => state = 4,
            4 if parts.len() == 1 && keyword == "endfacet" => {
                if parsed >= limits.triangles {
                    return Err("triangle budget".into());
                }
                let tri = [vertices[0], vertices[1], vertices[2]];
                for p in tri {
                    for a in 0..3 {
                        lower[a] = lower[a].min(p[a] as f64);
                        upper[a] = upper[a].max(p[a] as f64);
                    }
                }
                batch.push(tri);
                parsed += 1;
                state = 0;
                if batch.len() == limits.chunk {
                    consume(&batch)?;
                    batch.clear();
                }
            }
            _ => return Err("invalid ASCII STL facet".into()),
        }
    }
    if state != 0 || parsed == 0 {
        return Err("truncated ASCII STL".into());
    }
    if !batch.is_empty() {
        consume(&batch)?;
    }
    Ok((parsed, lower, upper))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rejects_growth_past_opened_source_length() {
        let path =
            std::env::temp_dir().join(format!("printstash-ascii-limit-{}.stl", std::process::id()));
        std::fs::write(&path, b"solid x\n# appended after source admission\n").unwrap();
        let reader = BufReader::new(File::open(&path).unwrap());
        let result = read(
            reader,
            Limits {
                triangles: 10,
                source_bytes: 8,
                lines: 100,
                line_bytes: 65536,
                deadline: Instant::now() + std::time::Duration::from_secs(5),
                chunk: 1,
            },
            |_| panic!("must not consume a triangle beyond the admitted source"),
        );
        std::fs::remove_file(path).unwrap();
        assert_eq!(result.unwrap_err(), "source byte budget");
    }
}
