//! Bounded STL depth pass with the reference centered-tile budget semantics.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

const BATCH: usize = 250_000;

struct Face {
    points: [f32; 9],
    denominator: f32,
    left: usize,
    top: usize,
    width: usize,
    height: usize,
}

impl Face {
    fn paint(&self, closest: &mut [f64], width: usize, offset: usize, count: usize) {
        let [ax, ay, az, bx, by, bz, cx, cy, cz] = self.points;
        let a = (by - cy) as f64;
        let b = (cx - bx) as f64;
        let c = (cy - ay) as f64;
        let d = (ax - cx) as f64;
        for position in offset..offset + count {
            let x = self.left + position % self.width;
            let y = self.top + position / self.width;
            let fx = x as f64 + 0.5;
            let fy = y as f64 + 0.5;
            let w0 = (a * (fx - cx as f64) + b * (fy - cy as f64)) / self.denominator as f64;
            let w1 = (c * (fx - cx as f64) + d * (fy - cy as f64)) / self.denominator as f64;
            let w2 = 1.0 - w0 - w1;
            if w0 >= 0.0 && w1 >= 0.0 && w2 >= 0.0 {
                let z = (w0 * az as f64 + w1 * bz as f64) + w2 * cz as f64;
                let pixel = y * width + x;
                if z < closest[pixel] {
                    closest[pixel] = z;
                }
            }
        }
    }
}

#[pyfunction]
pub fn streaming_depth<'py>(
    py: Python<'py>,
    triangles: &[u8],
    depth: &[u8],
    width: usize,
    height: usize,
    remaining: usize,
) -> PyResult<(Bound<'py, PyBytes>, usize)> {
    let pixels = width
        .checked_mul(height)
        .filter(|&n| n > 0 && n <= crate::MAX_PIXELS)
        .ok_or_else(|| PyValueError::new_err("invalid image dimensions"))?;
    if depth.len() != pixels * 4
        || !triangles.len().is_multiple_of(36)
        || triangles.len() / 36 > 2_000_000
        || remaining > 20_000_000
    {
        return Err(PyValueError::new_err("invalid streaming buffers or budget"));
    }
    let (values, used) = py
        .detach(|| -> Result<(Vec<f64>, usize), &'static str> {
            let mut closest: Vec<f64> = depth
                .as_chunks::<4>()
                .0
                .iter()
                .map(|v| f32::from_ne_bytes(*v) as f64)
                .collect();
            if closest.iter().any(|v| v.is_nan()) {
                return Err("invalid depth values");
            }
            let used = draw_depth(triangles, &mut closest, width, height, remaining)?;
            Ok((closest, used))
        })
        .map_err(PyValueError::new_err)?;
    let output = PyBytes::new_with(py, depth.len(), |output| {
        py.detach(|| {
            for (bytes, value) in output.as_chunks_mut::<4>().0.iter_mut().zip(values) {
                bytes.copy_from_slice(&(value as f32).to_ne_bytes());
            }
        });
        Ok(())
    })?;
    Ok((output, used))
}

/// Paint into owned depth without round-tripping the framebuffer through Python.
pub(crate) fn draw_depth(
    triangles: &[u8],
    closest: &mut [f64],
    width: usize,
    height: usize,
    remaining: usize,
) -> Result<usize, &'static str> {
    let mut faces = Vec::with_capacity(triangles.len() / 36);
    for bytes in triangles.as_chunks::<36>().0 {
        let p: [f32; 9] = std::array::from_fn(|i| {
            f32::from_ne_bytes(bytes[i * 4..i * 4 + 4].try_into().unwrap())
        });
        if !p.iter().all(|v| v.is_finite()) {
            return Err("triangle coordinates must be finite");
        }
        let [ax, ay, _, bx, by, _, cx, cy, _] = p;
        let denominator = (by - cy) * (ax - cx) + (cx - bx) * (ay - cy);
        if denominator.abs() <= 1e-9 {
            continue;
        }
        if !denominator.is_finite() {
            return Err("triangle arithmetic overflow");
        }
        let left = ax.min(bx).min(cx).floor().clamp(0.0, (width - 1) as f32) as usize;
        let right = ax.max(bx).max(cx).ceil().clamp(0.0, (width - 1) as f32) as usize;
        let top = ay.min(by).min(cy).floor().clamp(0.0, (height - 1) as f32) as usize;
        let bottom = ay.max(by).max(cy).ceil().clamp(0.0, (height - 1) as f32) as usize;
        faces.push(Face {
            points: p,
            denominator,
            left,
            top,
            width: right - left + 1,
            height: bottom - top + 1,
        });
    }
    let mut index = 0;
    let mut used = 0;
    while index < faces.len() && used < remaining {
        let available = BATCH.min(remaining - used);
        let face = &mut faces[index];
        let area = face.width * face.height;
        if area > available {
            if area > remaining - used {
                let w = face.width.min(remaining - used);
                let h = face.height.min(((remaining - used) / w).max(1));
                face.left += (face.width - w) / 2;
                face.top += (face.height - h) / 2;
                face.width = w;
                face.height = h;
            }
            let area = face.width * face.height;
            for offset in (0..area).step_by(BATCH) {
                let count = BATCH.min(area - offset);
                face.paint(closest, width, offset, count);
                // The reference commits float32 depth after each candidate batch.
                closest.iter_mut().for_each(|v| *v = (*v as f32) as f64);
                used += count;
            }
            index += 1;
        } else {
            let mut count = 0;
            while index < faces.len() {
                let face = &faces[index];
                let area = face.width * face.height;
                if count + area > available {
                    break;
                }
                face.paint(closest, width, 0, area);
                count += area;
                index += 1;
            }
            closest.iter_mut().for_each(|v| *v = (*v as f32) as f64);
            used += count;
        }
    }
    Ok(used)
}
