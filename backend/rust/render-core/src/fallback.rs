//! Sampled STL coverage with bounded work and no mesh-sized pixel expansion.
use crate::{
    images,
    job::Profile,
    stl_sample::{self, Sample},
};
use std::{path::Path, time::Instant};

type Point = [f64; 3];
type Triangle = [Point; 3];
fn sub(a: Point, b: Point) -> Point {
    std::array::from_fn(|i| a[i] - b[i])
}
fn transform(v: Point, r: [Point; 3]) -> Point {
    std::array::from_fn(|a| (v[0] * r[a][0] + v[1] * r[a][1]) + v[2] * r[a][2])
}
fn cross(a: Point, b: Point) -> Point {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn length(v: Point) -> f64 {
    ((v[0] * v[0] + v[1] * v[1]) + v[2] * v[2]).sqrt()
}

struct Canvas {
    depth: Vec<f64>,
    rgba: Vec<u8>,
    width: usize,
    height: usize,
    used: usize,
    limit: usize,
}
impl Canvas {
    fn draw(&mut self, t: Triangle, color: [u8; 3]) {
        if self.used == self.limit {
            return;
        }
        let [a, b, c] = t;
        let denom = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1]);
        if denom.abs() <= 1e-9 || !denom.is_finite() {
            return;
        }
        let mut left = a[0]
            .min(b[0])
            .min(c[0])
            .floor()
            .clamp(0.0, (self.width - 1) as f64) as usize;
        let right = a[0]
            .max(b[0])
            .max(c[0])
            .ceil()
            .clamp(0.0, (self.width - 1) as f64) as usize;
        let mut top = a[1]
            .min(b[1])
            .min(c[1])
            .floor()
            .clamp(0.0, (self.height - 1) as f64) as usize;
        let bottom = a[1]
            .max(b[1])
            .max(c[1])
            .ceil()
            .clamp(0.0, (self.height - 1) as f64) as usize;
        let (mut w, mut h) = (right - left + 1, bottom - top + 1);
        let remaining = self.limit - self.used;
        if w * h > remaining {
            let nw = w.min(remaining);
            let nh = h.min((remaining / nw).max(1));
            left += (w - nw) / 2;
            top += (h - nh) / 2;
            w = nw;
            h = nh;
        }
        self.used += w * h;
        for y in top..top + h {
            for x in left..left + w {
                let fx = x as f64 + 0.5;
                let fy = y as f64 + 0.5;
                let w0 = ((b[1] - c[1]) * (fx - c[0]) + (c[0] - b[0]) * (fy - c[1])) / denom;
                let w1 = ((c[1] - a[1]) * (fx - c[0]) + (a[0] - c[0]) * (fy - c[1])) / denom;
                let w2 = 1.0 - w0 - w1;
                if w0 >= 0.0 && w1 >= 0.0 && w2 >= 0.0 {
                    let z = (w0 * a[2] + w1 * b[2]) + w2 * c[2];
                    let pixel = y * self.width + x;
                    if z < self.depth[pixel] {
                        self.depth[pixel] = z;
                        self.rgba[pixel * 4..pixel * 4 + 3].copy_from_slice(&color);
                        self.rgba[pixel * 4 + 3] = 255;
                    }
                }
            }
        }
    }
}

pub struct FallbackPreview {
    pub image: Vec<u8>,
    pub sample: Sample,
    pub candidates: usize,
    /// Sampling, framing, coverage, encoding wall seconds.
    pub seconds: [f64; 4],
}

pub fn render(
    path: &Path,
    width: usize,
    height: usize,
    budget: usize,
    profile: Profile,
) -> Result<FallbackPreview, String> {
    profile.validate()?;
    if !(1..=2048).contains(&width) || !(1..=2048).contains(&height) {
        return Err("invalid fallback dimensions".into());
    }
    let start = Instant::now();
    let sample = stl_sample::read(path, budget)?;
    let mut seconds = [start.elapsed().as_secs_f64(), 0.0, 0.0, 0.0];
    let start = Instant::now();
    let center: Point = std::array::from_fn(|a| (sample.lower[a] + sample.upper[a]) * 0.5);
    let rotation = profile.rotation_bounds(sample.lower, sample.upper);
    let mut lo = [f64::INFINITY; 3];
    let mut hi = [f64::NEG_INFINITY; 3];
    for bits in 0..8 {
        let corner = std::array::from_fn(|a| {
            if bits & (1 << a) == 0 {
                sample.lower[a]
            } else {
                sample.upper[a]
            }
        });
        let v = transform(sub(corner, center), rotation);
        for a in 0..3 {
            lo[a] = lo[a].min(v[a]);
            hi[a] = hi[a].max(v[a]);
        }
    }
    let extent_x = (hi[0] - lo[0]).max(1e-6);
    let extent_y = (hi[1] - lo[1]).max(1e-6);
    let scale = (width as f64 * 0.64 / extent_x).min(height as f64 * 0.64 / extent_y);
    let mid: Point = std::array::from_fn(|a| (hi[a] + lo[a]) * 0.5);
    if !scale.is_finite() || !mid.iter().all(|v| v.is_finite()) {
        return Err("invalid fallback projection".into());
    }
    let cw = width.min(64.max(width / 2));
    let ch = height.min(48.max(height / 2));
    let sx = cw as f64 / width as f64;
    let sy = ch as f64 / height as f64;
    let area = (extent_x * extent_y * scale * scale * sx * sy).max(1.0);
    let radius = (0.7 * (area / sample.triangles.len() as f64).sqrt()).clamp(0.65, 1.0);
    let sparse = sample.count > sample.triangles.len() as u64 || !sample.complete;
    let mut canvas = Canvas {
        depth: vec![f64::INFINITY; cw * ch],
        rgba: vec![0; cw * ch * 4],
        width: cw,
        height: ch,
        used: 0,
        limit: 2_000_000,
    };
    let light = [-0.45f32, 0.6, 1.0];
    let norm = ((light[0] * light[0] + light[1] * light[1]) + light[2] * light[2]).sqrt();
    let light = light.map(|v| (v / norm) as f64);
    let base = profile.albedo.map(|v| ((v as f32) * 255.0) as f64);
    seconds[1] = start.elapsed().as_secs_f64();
    let start = Instant::now();
    let mut batch = Vec::with_capacity(2048);
    for chunk in sample.triangles.chunks(2048) {
        if canvas.used == canvas.limit {
            break;
        }
        batch.clear();
        for tri in chunk {
            let v = tri.map(|p| transform(sub(p.map(|v| v as f64), center), rotation));
            let raw = cross(sub(v[1], v[0]), sub(v[2], v[0]));
            let len = length(raw);
            let mut screen = v.map(|p| {
                [
                    (p[0] - mid[0]) * scale + width as f64 * 0.5,
                    height as f64 * 0.5 - (p[1] - mid[1]) * scale,
                    p[2],
                ]
            });
            let area = ((screen[1][0] - screen[0][0]) * (screen[2][1] - screen[0][1])
                - (screen[2][0] - screen[0][0]) * (screen[1][1] - screen[0][1]))
                .abs();
            if !screen.iter().flatten().all(|v| v.is_finite())
                || !area.is_finite()
                || area <= 1e-9
                || len <= 1e-12
            {
                continue;
            }
            let n = raw.map(|v| if raw[2] >= 0.0 { v / len } else { -v / len });
            let diffuse = ((n[0] * light[0] + n[1] * light[1]) + n[2] * light[2]).clamp(0.0, 1.0);
            let color = base.map(|v| (v * (0.32 + diffuse * 0.68)).clamp(0.0, 255.0) as u8);
            for p in &mut screen {
                p[0] *= sx;
                p[1] *= sy;
            }
            batch.push((screen, color));
        }
        if sparse {
            for (tri, color) in &batch {
                let center: Point =
                    std::array::from_fn(|a| ((tri[0][a] + tri[1][a]) + tri[2][a]) / 3.0);
                let mut splat = [center; 3];
                splat[0][1] -= radius;
                splat[1][0] += radius;
                splat[1][1] += radius;
                splat[2][0] -= radius;
                splat[2][1] += radius;
                canvas.draw(splat, *color);
            }
        }
        for (tri, color) in &batch {
            canvas.draw(*tri, *color);
        }
    }
    seconds[2] = start.elapsed().as_secs_f64();
    if !canvas.depth.iter().any(|v| v.is_finite()) {
        return Err("no visible STL facets".into());
    }
    let start = Instant::now();
    let image = images::process_image(
        &canvas.rgba,
        cw as u32,
        ch as u32,
        width as u32,
        height as u32,
        "bilinear",
        false,
        false,
        "PNG",
    )?;
    seconds[3] = start.elapsed().as_secs_f64();
    Ok(FallbackPreview {
        image,
        sample,
        candidates: canvas.used,
        seconds,
    })
}
