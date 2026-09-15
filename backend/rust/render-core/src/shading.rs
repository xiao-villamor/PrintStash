use crate::number;
fn dot(n: &[f64; 3], direction: &[f64]) -> f64 {
    ((n[0] * direction[0] + n[1] * direction[1]) + n[2] * direction[2]).clamp(0.0, 1.0)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn shade_into(
    records: &[u8],
    triangles: &[u8],
    itemsize: usize,
    normals: &[u8],
    normal_itemsize: usize,
    width: usize,
    pixels: usize,
    lighting: &[f64; 31],
    output: &mut [u8],
) -> Result<(), &'static str> {
    let round = |v: f64| if itemsize == 4 { (v as f32) as f64 } else { v };
    let faces = triangles.len() / (9 * itemsize);
    for (record, rgb) in records.chunks_exact(24).zip(output.chunks_exact_mut(3)) {
        let pixel = u64::from_ne_bytes(record[..8].try_into().unwrap());
        let face = u64::from_ne_bytes(record[8..16].try_into().unwrap());
        if pixel >= pixels as u64 || face >= faces as u64 {
            return Err("invalid fragment index");
        }
        let pixel = pixel as usize;
        let face = face as usize;
        let mut t = [0.0; 9];
        for (i, value) in t.iter_mut().enumerate() {
            *value = number(&triangles[(face * 9 + i) * itemsize..], itemsize);
        }
        let [ax, ay, _, bx, by, _, cx, cy, _] = t;
        let denom =
            round(round(round(by - cy) * round(ax - cx)) + round(round(cx - bx) * round(ay - cy)));
        if !denom.is_finite() || denom == 0.0 {
            return Err("degenerate fragment triangle");
        }
        let fx = (pixel % width) as f64 + 0.5;
        let fy = (pixel / width) as f64 + 0.5;
        let w0 = (round(by - cy) * (fx - cx) + round(cx - bx) * (fy - cy)) / denom;
        let w1 = (round(cy - ay) * (fx - cx) + round(ax - cx) * (fy - cy)) / denom;
        let w2 = 1.0 - w0 - w1;
        let mut n = [0.0; 3];
        for (axis, value) in n.iter_mut().enumerate() {
            let read = |corner: usize| {
                number(
                    &normals[(face * 9 + corner * 3 + axis) * normal_itemsize..],
                    normal_itemsize,
                )
            };
            *value = (w0 * read(0) + w1 * read(1)) + w2 * read(2);
        }
        let length = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
        if !length.is_finite() {
            return Err("interpolated normals must be finite");
        }
        if length != 0.0 {
            for value in &mut n {
                *value /= length;
            }
        }
        let diffuse_key = dot(&n, &lighting[..3]);
        let diffuse_fill = dot(&n, &lighting[3..6]);
        let fresnel = (1.0 - n[2].clamp(0.0, 1.0)).powf(lighting[26]);
        let specular = dot(&n, &lighting[6..9]).powf(lighting[27]);
        for axis in 0..3 {
            let diffuse = ((lighting[24] + (lighting[21] * diffuse_key) * lighting[12 + axis])
                + (lighting[22] * diffuse_fill) * lighting[15 + axis])
                * lighting[9 + axis];
            let color = (diffuse + (lighting[23] * fresnel) * lighting[18 + axis])
                + lighting[25] * specular;
            rgb[axis] = (lighting[28 + axis] * color.clamp(0.0, 1.0)).clamp(0.0, 255.0) as u8;
        }
    }
    Ok(())
}
