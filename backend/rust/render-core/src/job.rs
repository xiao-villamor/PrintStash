//! One owned rendering job. The caller supplies recipe data, never stage callbacks.
use crate::{images, PreparedPreview};
use std::time::Instant;

#[derive(Clone, Copy)]
pub struct Profile {
    pub margin: f64,
    pub azimuth: f64,
    pub elevation: f64,
    pub flat_tilt: f64,
    pub flat_ratio: f64,
    pub albedo: [f64; 3],
    pub supersampling: [usize; 3],
}
impl Profile {
    pub fn validate(&self) -> Result<(), &'static str> {
        if ![
            self.margin,
            self.azimuth,
            self.elevation,
            self.flat_tilt,
            self.flat_ratio,
        ]
        .iter()
        .all(|v| v.is_finite())
            || !(0.0..0.5).contains(&self.margin)
            || !(0.0..=1.0).contains(&self.flat_ratio)
            || !self
                .albedo
                .iter()
                .all(|v| v.is_finite() && (0.0..=1.0).contains(v))
            || self.supersampling[0] > 4096
            || !self.supersampling[1..].iter().all(|v| (1..=4).contains(v))
        {
            return Err("invalid preview profile");
        }
        Ok(())
    }
    pub fn rotation(&self, lower: [f32; 3], upper: [f32; 3]) -> [[f64; 3]; 3] {
        self.rotation_extents(std::array::from_fn(|a| (upper[a] - lower[a]) as f64))
    }
    pub fn rotation_bounds(&self, lower: [f64; 3], upper: [f64; 3]) -> [[f64; 3]; 3] {
        self.rotation_extents(std::array::from_fn(|a| upper[a] - lower[a]))
    }
    fn rotation_extents(&self, extents: [f64; 3]) -> [[f64; 3]; 3] {
        let thin = (1..3).fold(0, |i, j| if extents[j] < extents[i] { j } else { i });
        let broad = (0..3)
            .filter(|a| *a != thin)
            .map(|a| extents[a])
            .fold(0.0f64, f64::max);
        let base = [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, -1.0, 0.0]];
        if broad > 1e-6 && extents[thin] / broad <= self.flat_ratio {
            let base = match thin {
                0 => [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [-1.0, 0.0, 0.0]],
                1 => base,
                _ => [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, -1.0]],
            };
            multiply(tilt(self.flat_tilt), base)
        } else {
            let (s, c) = self.azimuth.to_radians().sin_cos();
            let spin = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]];
            multiply(multiply(tilt(-self.elevation), base), spin)
        }
    }
    fn lighting(&self, matte: bool) -> [f64; 31] {
        let key = normalize([-0.5, 0.65, 1.0]);
        let fill = normalize([0.55, -0.25, 0.55]);
        let half = normalize([key[0], key[1], key[2] + 1.0]);
        let mut result = [0.0; 31];
        for (target, source) in result[..21].chunks_exact_mut(3).zip([
            key,
            fill,
            half,
            self.albedo,
            [1.0, 0.98, 0.95],
            [0.55, 0.62, 0.78],
            [0.85, 0.92, 1.0],
        ]) {
            target.copy_from_slice(&source);
        }
        result[21..].copy_from_slice(&[
            1.05,
            0.30,
            0.22,
            0.30,
            if matte { 0.0 } else { 0.22 },
            3.0,
            32.0,
            255.0,
            255.0,
            255.0,
        ]);
        result
    }
}
fn normalize(v: [f64; 3]) -> [f64; 3] {
    let length = ((v[0] * v[0] + v[1] * v[1]) + v[2] * v[2]).sqrt();
    v.map(|x| x / length)
}
fn tilt(degrees: f64) -> [[f64; 3]; 3] {
    let (s, c) = degrees.to_radians().sin_cos();
    [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]
}
fn multiply(a: [[f64; 3]; 3], b: [[f64; 3]; 3]) -> [[f64; 3]; 3] {
    std::array::from_fn(|i| {
        std::array::from_fn(|j| (a[i][0] * b[0][j] + a[i][1] * b[1][j]) + a[i][2] * b[2][j])
    })
}
fn determinant(r: [[f64; 3]; 3]) -> f64 {
    r[0][0] * (r[1][1] * r[2][2] - r[1][2] * r[2][1])
        - r[0][1] * (r[1][0] * r[2][2] - r[1][2] * r[2][0])
        + r[0][2] * (r[1][0] * r[2][1] - r[1][1] * r[2][0])
}

pub struct RenderOptions<'a> {
    pub width: usize,
    pub height: usize,
    pub chunk: usize,
    pub format: &'a str,
    pub profile: Profile,
    pub rotation: Option<[[f64; 3]; 3]>,
    pub matte: bool,
}

pub struct RenderedPreview {
    pub image: Vec<u8>,
    /// Non-overlapping wall seconds: preparation, camera, raster, RGBA, encoding.
    pub seconds: [f64; 5],
}

pub fn render(
    vertices: &[u8],
    faces: &[u8],
    options: &RenderOptions<'_>,
) -> Result<RenderedPreview, String> {
    options.profile.validate()?;
    let p = options.profile;
    let factor = p.supersampling[if options.width <= p.supersampling[0] {
        1
    } else {
        2
    }];
    if options.width == 0
        || options.height == 0
        || options.width > 4096 / factor
        || options.height > 4096 / factor
        || !matches!(options.format, "PNG" | "WEBP" | "RGB")
    {
        return Err("invalid preview dimensions or format".into());
    }
    let mut seconds = [0.0; 5];
    let started = Instant::now();
    let mesh = PreparedPreview::new(vertices, faces, options.chunk)?;
    seconds[0] = started.elapsed().as_secs_f64();
    let started = Instant::now();
    let rotation = options
        .rotation
        .unwrap_or_else(|| p.rotation(mesh.lower, mesh.upper));
    let lighting = p.lighting(options.matte);
    let flat = p.albedo.map(|v| (v * 0.6 * 255.0).clamp(0.0, 255.0) as u8);
    seconds[1] = started.elapsed().as_secs_f64();
    let started = Instant::now();
    let frame = mesh.render_frame(
        rotation,
        determinant(rotation),
        options.width * factor,
        options.height * factor,
        p.margin,
        lighting,
        flat,
    )?;
    seconds[2] = started.elapsed().as_secs_f64();
    drop(mesh);
    let started = Instant::now();
    let rgba = frame.rgba()?;
    drop(frame);
    seconds[3] = started.elapsed().as_secs_f64();
    let started = Instant::now();
    let image = images::process_image(
        &rgba,
        (options.width * factor) as u32,
        (options.height * factor) as u32,
        options.width as u32,
        options.height as u32,
        "lanczos",
        true,
        true,
        options.format,
    )?;
    seconds[4] = started.elapsed().as_secs_f64();
    Ok(RenderedPreview { image, seconds })
}

/// Share preparation across a bounded set of inference views. Only one frame is
/// live at a time; callers receive opaque RGB, without intermediate image codecs.
#[allow(clippy::too_many_arguments)]
pub fn render_views(
    vertices: &[u8],
    faces: &[u8],
    width: usize,
    height: usize,
    chunk: usize,
    profile: Profile,
    rotations: &[Option<[[f64; 3]; 3]>],
) -> Result<Vec<Vec<u8>>, String> {
    profile.validate()?;
    if !(1..=512).contains(&width)
        || !(1..=512).contains(&height)
        || !(1..=6).contains(&rotations.len())
    {
        return Err("invalid multiview budget".into());
    }
    let factor = profile.supersampling[if width <= profile.supersampling[0] {
        1
    } else {
        2
    }];
    let mesh = PreparedPreview::new(vertices, faces, chunk)?;
    let lighting = profile.lighting(true);
    let flat = profile
        .albedo
        .map(|v| (v * 0.6 * 255.0).clamp(0.0, 255.0) as u8);
    let mut result = Vec::with_capacity(rotations.len());
    for rotation in rotations {
        let rotation = rotation.unwrap_or_else(|| profile.rotation(mesh.lower, mesh.upper));
        let frame = mesh.render_frame(
            rotation,
            determinant(rotation),
            width * factor,
            height * factor,
            profile.margin,
            lighting,
            flat,
        )?;
        let rgba = frame.rgba()?;
        drop(frame);
        result.push(images::process_image(
            &rgba,
            (width * factor) as u32,
            (height * factor) as u32,
            width as u32,
            height as u32,
            "lanczos",
            true,
            true,
            "RGB",
        )?);
    }
    Ok(result)
}
