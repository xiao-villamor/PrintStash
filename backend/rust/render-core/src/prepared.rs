//! Owned mesh preparation and crease-aware drawing without per-batch Python arrays.
use crate::Frame;

fn sub(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    std::array::from_fn(|i| a[i] - b[i])
}
fn cross(a: [f32; 3], b: [f32; 3]) -> [f32; 3] {
    [
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    ]
}
fn length(a: [f32; 3]) -> f32 {
    ((a[0] * a[0] + a[1] * a[1]) + a[2] * a[2]).sqrt()
}
fn normal(a: [f32; 3]) -> [f32; 3] {
    let n = length(a);
    a.map(|v| v / if n == 0.0 { 1.0 } else { n })
}
fn bounds(points: &[[f32; 3]]) -> ([f32; 3], [f32; 3]) {
    let mut lo = [f32::INFINITY; 3];
    let mut hi = [f32::NEG_INFINITY; 3];
    for p in points {
        for a in 0..3 {
            lo[a] = lo[a].min(p[a]);
            hi[a] = hi[a].max(p[a]);
        }
    }
    (lo, hi)
}
fn bytes32(values: &[[f32; 3]]) -> Vec<u8> {
    values
        .iter()
        .flatten()
        .flat_map(|v| v.to_ne_bytes())
        .collect()
}

pub struct PreparedPreview {
    vertices: Vec<[f32; 3]>,
    faces: Vec<[u32; 3]>,
    positions: Vec<u32>,
    normals: Vec<[f64; 3]>,
    chunk: usize,

    pub lower: [f32; 3],

    pub upper: [f32; 3],
}

impl PreparedPreview {
    pub fn from_parts(
        vertices: Vec<[f32; 3]>,
        faces: Vec<[u32; 3]>,
        chunk: usize,
    ) -> Result<Self, &'static str> {
        let packed_vertices = bytes32(&vertices);
        let packed_faces = faces
            .iter()
            .flatten()
            .flat_map(|value| (*value as u64).to_ne_bytes())
            .collect::<Vec<_>>();
        Self::new(&packed_vertices, &packed_faces, chunk)
    }

    pub fn new(vertices: &[u8], faces: &[u8], chunk: usize) -> Result<Self, &'static str> {
        if vertices.is_empty()
            || !vertices.len().is_multiple_of(12)
            || vertices.len() / 12 > 6_000_000
            || faces.is_empty()
            || !faces.len().is_multiple_of(24)
            || faces.len() / 24 > 2_000_000
            || chunk == 0
        {
            return Err("invalid mesh preparation buffers");
        }
        let mut mesh = (|| -> Result<Self, &'static str> {
            let mut points: Vec<[f32; 3]> = vertices
                .as_chunks::<12>()
                .0
                .iter()
                .map(|p| {
                    std::array::from_fn(|a| {
                        f32::from_ne_bytes(p[a * 4..a * 4 + 4].try_into().unwrap())
                    })
                })
                .collect();
            if !points.iter().flatten().all(|v| v.is_finite()) {
                return Err("non-finite vertex");
            }
            let mut indices = Vec::with_capacity(faces.len() / 24);
            for face in faces.as_chunks::<24>().0 {
                let raw: [u64; 3] = std::array::from_fn(|a| {
                    u64::from_ne_bytes(face[a * 8..a * 8 + 8].try_into().unwrap())
                });
                if raw.iter().any(|v| *v >= points.len() as u64) {
                    return Err("invalid face index");
                }
                indices.push(raw.map(|v| v as u32));
            }
            let (lo, hi) = bounds(&points);
            let center: [f32; 3] = std::array::from_fn(|a| (hi[a] + lo[a]) * 0.5);
            for p in &mut points {
                *p = sub(*p, center);
            }
            let (lower, upper) = bounds(&points);
            let extent = length(sub(upper, lower));
            if !extent.is_finite() || !center.iter().all(|v| v.is_finite()) {
                return Err("mesh arithmetic overflow");
            }
            let step = ((if extent == 0.0 { 1.0 } else { extent as f64 }) * 1e-5) as f32;
            if step <= 0.0 {
                return Err("mesh quantization underflow");
            }
            let quantize = |p: [f32; 3]| p.map(|v| (v / step).round_ties_even() as i64);
            let mut qmin = [i64::MAX; 3];
            let mut qmax = [i64::MIN; 3];
            for p in &points {
                let q = quantize(*p);
                for a in 0..3 {
                    qmin[a] = qmin[a].min(q[a]);
                    qmax[a] = qmax[a].max(q[a]);
                }
            }
            let span: [i64; 3] = std::array::from_fn(|a| qmax[a] - qmin[a] + 1);
            let mut keys: Vec<(i64, u32)> = points
                .iter()
                .enumerate()
                .map(|(i, p)| {
                    let q = quantize(*p);
                    (
                        ((q[0] - qmin[0]) * span[1] + q[1] - qmin[1]) * span[2] + q[2] - qmin[2],
                        i as u32,
                    )
                })
                .collect();
            keys.sort_unstable_by_key(|pair| pair.0);
            let mut positions = vec![0u32; points.len()];
            let mut position = 0;
            let mut previous = None;
            for (key, index) in keys {
                if previous.is_some_and(|p| p != key) {
                    position += 1;
                }
                positions[index as usize] = position;
                previous = Some(key);
            }
            Ok(Self {
                vertices: points,
                faces: indices,
                positions,
                normals: Vec::new(),
                lower,
                upper,
                chunk: chunk.min(2_000_000),
            })
        })()?;
        let (packed, positions) = {
            (
                bytes32(&mesh.vertices),
                mesh.positions
                    .iter()
                    .flat_map(|v| (*v as u64).to_ne_bytes())
                    .collect::<Vec<_>>(),
            )
        };
        let count = mesh.positions.iter().copied().max().unwrap_or(0) as usize + 1;
        mesh.normals =
            crate::normals::smooth_normals(&packed, faces, &positions, count, mesh.chunk)?;
        Ok(mesh)
    }
    #[allow(clippy::too_many_arguments)]
    pub fn render_frame(
        &self,
        rotation: [[f64; 3]; 3],
        handedness: f64,
        width: usize,
        height: usize,
        margin: f64,
        lighting: [f64; 31],
        flat_color: [u8; 3],
    ) -> Result<Frame, &'static str> {
        if !rotation.iter().flatten().all(|v| v.is_finite())
            || !handedness.is_finite()
            || (handedness.abs() - 1.0).abs() > 1e-6
            || !margin.is_finite()
            || !(0.0..0.5).contains(&margin)
        {
            return Err("invalid mesh projection");
        }
        for a in 0..3 {
            for b in 0..3 {
                let dot = (rotation[a][0] * rotation[b][0] + rotation[a][1] * rotation[b][1])
                    + rotation[a][2] * rotation[b][2];
                if (dot - if a == b { 1.0 } else { 0.0 }).abs() > 1e-6 {
                    return Err("invalid mesh rotation");
                }
            }
        }
        let determinant = rotation[0][0]
            * (rotation[1][1] * rotation[2][2] - rotation[1][2] * rotation[2][1])
            - rotation[0][1] * (rotation[1][0] * rotation[2][2] - rotation[1][2] * rotation[2][0])
            + rotation[0][2] * (rotation[1][0] * rotation[2][1] - rotation[1][1] * rotation[2][0]);
        if (determinant - handedness).abs() > 1e-6 {
            return Err("invalid mesh handedness");
        }
        let mut frame = Frame::new(width, height)?;
        let (view, screen) = {
            let r = rotation.map(|row| row.map(|v| v as f32));
            let view: Vec<[f32; 3]> = self
                .vertices
                .iter()
                .map(|v| {
                    std::array::from_fn(|a| (v[0] * r[a][0] + v[1] * r[a][1]) + v[2] * r[a][2])
                })
                .collect();
            let (lo, hi) = bounds(&view);
            let scale = ((width as f64 * (1.0 - 2.0 * margin)
                / (hi[0] as f64 - lo[0] as f64).max(1e-6))
            .min(height as f64 * (1.0 - 2.0 * margin) / (hi[1] as f64 - lo[1] as f64).max(1e-6)))
                as f32;
            let midx = ((lo[0] as f64 + hi[0] as f64) * 0.5) as f32;
            let midy = ((lo[1] as f64 + hi[1] as f64) * 0.5) as f32;
            let screen: Vec<[f32; 3]> = view
                .iter()
                .map(|v| {
                    [
                        (v[0] - midx) * scale + width as f32 * 0.5,
                        height as f32 * 0.5 - (v[1] - midy) * scale,
                        v[2],
                    ]
                })
                .collect();
            (view, screen)
        };
        let mut visible = 0;
        for faces in self.faces.chunks(self.chunk) {
            let (triangles, normals, count) = {
                let mut triangles = Vec::with_capacity(faces.len() * 36);
                let mut normals = Vec::with_capacity(faces.len() * 72);
                let mut count = 0;
                for face in faces {
                    let ids = face.map(|v| v as usize);
                    let raw = cross(
                        sub(view[ids[1]], view[ids[0]]),
                        sub(view[ids[2]], view[ids[0]]),
                    );
                    if raw[2] as f64 * handedness >= 0.0 || length(raw) <= 1e-8 {
                        continue;
                    }
                    let face_normal = normal(cross(
                        sub(self.vertices[ids[1]], self.vertices[ids[0]]),
                        sub(self.vertices[ids[2]], self.vertices[ids[0]]),
                    ));
                    for id in ids {
                        let smooth = self.normals[self.positions[id] as usize];
                        let cos = (smooth[0] * face_normal[0] as f64
                            + smooth[1] * face_normal[1] as f64)
                            + smooth[2] * face_normal[2] as f64;
                        let t = ((cos - 0.75) / (0.92 - 0.75)).clamp(0.0, 1.0);
                        let t = t * t * (3.0 - 2.0 * t);
                        let mut n: [f64; 3] = std::array::from_fn(|a| {
                            t * smooth[a] + (1.0 - t) * face_normal[a] as f64
                        });
                        let length = ((n[0] * n[0] + n[1] * n[1]) + n[2] * n[2]).sqrt();
                        n.iter_mut()
                            .for_each(|v| *v /= if length == 0.0 { 1.0 } else { length });
                        let cvn: [f64; 3] = std::array::from_fn(|a| {
                            (n[0] * rotation[a][0] + n[1] * rotation[a][1]) + n[2] * rotation[a][2]
                        });
                        for v in cvn {
                            normals.extend_from_slice(
                                &(if cvn[2] >= 0.0 { v } else { -v }).to_ne_bytes(),
                            );
                        }
                        for v in screen[id] {
                            triangles.extend_from_slice(&v.to_ne_bytes());
                        }
                    }
                    count += 1;
                }
                (triangles, normals, count)
            };
            visible += count;
            frame.draw_phong(&triangles, 4, &normals, 8, lighting)?;
        }
        if visible == 0 {
            for faces in self.faces.chunks(self.chunk) {
                let triangles = {
                    faces
                        .iter()
                        .flatten()
                        .flat_map(|id| screen[*id as usize])
                        .flat_map(|v| v.to_ne_bytes())
                        .collect::<Vec<_>>()
                };
                frame.draw_flat(&triangles, 4, flat_color)?;
            }
        }
        Ok(frame)
    }
}
