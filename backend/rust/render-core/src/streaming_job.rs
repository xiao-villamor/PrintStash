//! Complete binary STL preview with two bounded passes and a fixed-size image.
use crate::{images, job::Profile, stl_source::NativeStlSource};
use std::{path::PathBuf, time::Instant};

pub struct Limits {
    pub triangles: usize,
    pub source_bytes: u64,
    pub candidates: usize,
    pub chunk: usize,
    pub timeout: f64,
    pub max_lines: usize,
    pub max_line_bytes: usize,
}
pub struct StreamedPreview {
    pub image: Vec<u8>,
    pub count: usize,
    pub scanned: u64,
    pub lower: [f64; 3],
    pub upper: [f64; 3],
    pub candidates: usize,
    /// Source scan, framing, depth pass, shading, encoding wall seconds.
    pub seconds: [f64; 5],
}
fn percentile(values: &mut [f64], fraction: f64) -> f64 {
    values.sort_unstable_by(f64::total_cmp);
    let index = (values.len() - 1) as f64 * fraction;
    let i = index.floor() as usize;
    let t = index - i as f64;
    let a = values[i];
    let b = values[(i + 1).min(values.len() - 1)];
    // NumPy's linear quantile switches ends to avoid cancellation near 1.
    if t >= 0.5 {
        b - (b - a) * (1.0 - t)
    } else {
        a + (b - a) * t
    }
}
pub fn render(
    path: PathBuf,
    width: usize,
    height: usize,
    limits: Limits,
    profile: Profile,
) -> Result<StreamedPreview, String> {
    profile.validate()?;
    if !(1..=2048).contains(&width) || !(1..=2048).contains(&height) {
        return Err("invalid streaming dimensions".into());
    }
    let start = Instant::now();
    let mut source = NativeStlSource::new(
        path,
        limits.triangles,
        limits.source_bytes,
        limits.chunk,
        limits.timeout,
    )?;
    source.set_ascii_limits(limits.max_lines, limits.max_line_bytes)?;
    let (count, scanned, lower, upper, reservoir) = source.analyze()?;
    let mut seconds = [start.elapsed().as_secs_f64(), 0.0, 0.0, 0.0, 0.0];
    let start = Instant::now();
    let mut lo = lower;
    let mut hi = upper;
    for a in 0..3 {
        let mut values: Vec<f64> = reservoir.iter().map(|p| p[a] as f64).collect();
        if !values.is_empty() {
            let l = percentile(&mut values, 0.005);
            let h = percentile(&mut values, 0.995);
            if h - l > ((upper[a] - lower[a]) * 0.01).max(1e-9) {
                lo[a] = l;
                hi[a] = h;
            }
        }
    }
    let center: [f64; 3] = std::array::from_fn(|a| (lo[a] + hi[a]) * 0.5);
    let rotation = profile.rotation_bounds(lo, hi);
    let mut vlo = [f64::INFINITY; 3];
    let mut vhi = [f64::NEG_INFINITY; 3];
    for bits in 0..8 {
        let v: [f64; 3] =
            std::array::from_fn(|a| (if bits & (1 << a) == 0 { lo[a] } else { hi[a] }) - center[a]);
        for a in 0..3 {
            let x = (v[0] * rotation[a][0] + v[1] * rotation[a][1]) + v[2] * rotation[a][2];
            vlo[a] = vlo[a].min(x);
            vhi[a] = vhi[a].max(x);
        }
    }
    let midpoint = std::array::from_fn(|a| ((vlo[a] + vhi[a]) * 0.5) as f32);
    let scale = (width as f64 * (1.0 - 2.0 * profile.margin) / (vhi[0] - vlo[0]).max(1e-6))
        .min(height as f64 * (1.0 - 2.0 * profile.margin) / (vhi[1] - vlo[1]).max(1e-6));
    let expanded_min = std::array::from_fn(|a| lo[a] - (hi[a] - lo[a]).max(1e-6) * 0.05);
    let expanded_max = std::array::from_fn(|a| hi[a] + (hi[a] - lo[a]).max(1e-6) * 0.05);
    seconds[1] = start.elapsed().as_secs_f64();
    let start = Instant::now();
    let (depth, candidates) = source.render_depth(
        width,
        height,
        limits.candidates,
        center.map(|v| v as f32),
        rotation.map(|r| r.map(|v| v as f32)),
        expanded_min,
        expanded_max,
        midpoint,
        scale as f32,
    )?;
    seconds[2] = start.elapsed().as_secs_f64();
    let start = Instant::now();
    let rgba = images::shade_depth(
        &depth,
        width as u32,
        height as u32,
        scale,
        profile.albedo.map(|v| v as f32),
    )?;
    seconds[3] = start.elapsed().as_secs_f64();
    drop(depth);
    let start = Instant::now();
    let image = images::process_image(
        &rgba,
        width as u32,
        height as u32,
        width as u32,
        height as u32,
        "lanczos",
        false,
        false,
        "PNG",
    )?;
    if image.len() > 8 * 1024 * 1024 {
        return Err("output budget".into());
    }
    seconds[4] = start.elapsed().as_secs_f64();
    Ok(StreamedPreview {
        image,
        count,
        scanned,
        lower,
        upper,
        candidates,
        seconds,
    })
}
