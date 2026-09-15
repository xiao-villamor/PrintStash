//! Rendering primitives with no Python, framework or runtime dependency.
#![forbid(unsafe_code)]
mod frame;
mod normals;
mod prepared;
pub use prepared::PreparedPreview;
mod shading;
pub use frame::Frame;
const MAX_PIXELS: usize = 16_777_216;
fn number(bytes: &[u8], itemsize: usize) -> f64 {
    if itemsize == 4 {
        f32::from_ne_bytes(bytes[..4].try_into().unwrap()) as f64
    } else {
        f64::from_ne_bytes(bytes[..8].try_into().unwrap())
    }
}

fn visibility_into(
    triangles: &[u8],
    itemsize: usize,
    closest: &mut [f64],
    winners: &mut [u32],
    width: usize,
    height: usize,
) -> Result<(usize, usize), &'static str> {
    if triangles.len() / (9 * itemsize) >= u32::MAX as usize {
        return Err("triangle index limit exceeded");
    }
    winners.fill(u32::MAX);
    let round = |v: f64| if itemsize == 4 { (v as f32) as f64 } else { v };
    let epsilon = if itemsize == 4 {
        f32::EPSILON as f64
    } else {
        f64::EPSILON
    };
    let mut candidates = 0usize;
    let mut visible = 0usize;

    for (face, bytes) in triangles.chunks_exact(9 * itemsize).enumerate() {
        let mut t = [0.0; 9];
        for (target, value) in t.iter_mut().zip(bytes.chunks_exact(itemsize)) {
            *target = number(value, itemsize);
        }
        if !t.iter().all(|v| v.is_finite()) {
            return Err("triangle coordinates must be finite");
        }
        let [ax, ay, az, bx, by, bz, cx, cy, cz] = t;
        let by_cy = round(by - cy);
        let cx_bx = round(cx - bx);
        let cy_ay = round(cy - ay);
        let ax_cx = round(ax - cx);
        let denom = round(round(by_cy * ax_cx) + round(cx_bx * round(ay - cy)));
        if !denom.is_finite() || denom.abs() <= round(1e-9) {
            continue;
        }
        let left = ax.min(bx).min(cx).floor().clamp(0.0, (width - 1) as f64) as usize;
        let right = ax.max(bx).max(cx).ceil().clamp(0.0, (width - 1) as f64) as usize;
        let top = ay.min(by).min(cy).floor().clamp(0.0, (height - 1) as f64) as usize;
        let bottom = ay.max(by).max(cy).ceil().clamp(0.0, (height - 1) as f64) as usize;
        let area = (right - left + 1) * (bottom - top + 1);
        let twice_area =
            round(round(round(bx - ax) * round(cy - ay)) - round(round(by - ay) * round(cx - ax)))
                .abs();
        let narrow = area > 256 && twice_area < area as f64 * 0.25;

        for y in top..=bottom {
            let fy = y as f64 + 0.5;
            let (mut row_left, mut row_right) = (left, right);
            if narrow {
                let mut low = f64::INFINITY;
                let mut high = f64::NEG_INFINITY;
                for (x0, y0, x1, y1) in [(ax, ay, bx, by), (bx, by, cx, cy), (cx, cy, ax, ay)] {
                    let slack = 8.0 * epsilon * y0.abs().max(y1.abs()).max(1.0);
                    if y1 != y0 && fy >= y0.min(y1) - slack && fy <= y0.max(y1) + slack {
                        let x = x0 + (fy - y0) * (x1 - x0) / (y1 - y0);
                        low = low.min(x);
                        high = high.max(x);
                    }
                }
                if low > high || high < left as f64 - 0.5 || low > right as f64 + 0.5 {
                    continue;
                }
                row_left = (low - 0.5).floor().clamp(left as f64, right as f64) as usize;
                row_right = (high - 0.5).ceil().clamp(left as f64, right as f64) as usize;
            }
            candidates = candidates.saturating_add(row_right - row_left + 1);
            for x in row_left..=row_right {
                let fx = x as f64 + 0.5;
                let w0 = (by_cy * (fx - cx) + cx_bx * (fy - cy)) / denom;
                let w1 = (cy_ay * (fx - cx) + ax_cx * (fy - cy)) / denom;
                let w2 = 1.0 - w0 - w1;
                if w0 >= 0.0 && w1 >= 0.0 && w2 >= 0.0 {
                    let z = w0 * az + w1 * bz + w2 * cz;
                    let pixel = y * width + x;
                    // Strict comparison preserves the first source face on ties
                    // and any equally close fragment from a previous chunk.
                    if z < closest[pixel] {
                        if winners[pixel] == u32::MAX {
                            visible += 1;
                        }
                        closest[pixel] = z;
                        winners[pixel] = face as u32;
                    }
                }
            }
        }
    }
    Ok((visible, candidates))
}

pub mod images;
pub mod job;

pub mod fallback;
pub mod stl_sample;

pub mod stl_source;
pub mod streaming_job;
mod streaming_preview;

mod stl_ascii;
