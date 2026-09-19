use crate::{number, shading, visibility_into, MAX_PIXELS};

/// An owned frame. A failed draw cannot publish a partially updated image.
pub struct Frame {
    width: usize,
    height: usize,
    depth: Vec<f64>,
    rgb: Vec<u8>,
    winners: Vec<u32>,
    failed: bool,
}

impl Frame {
    pub fn new(width: usize, height: usize) -> Result<Self, &'static str> {
        let pixels = width
            .checked_mul(height)
            .filter(|&n| n > 0 && n <= MAX_PIXELS)
            .ok_or("invalid image dimensions")?;
        Ok(Self {
            width,
            height,
            depth: vec![f64::INFINITY; pixels],
            rgb: vec![0; pixels * 3],
            winners: vec![u32::MAX; pixels],
            failed: false,
        })
    }

    #[allow(clippy::too_many_arguments)]
    pub fn draw_phong(
        &mut self,
        triangles: &[u8],
        itemsize: usize,
        normals: &[u8],
        normal_itemsize: usize,
        lighting: [f64; 31],
    ) -> Result<usize, &'static str> {
        self.check()?;
        if ![4, 8].contains(&itemsize) || ![4, 8].contains(&normal_itemsize) {
            return Err("unsupported float width");
        }
        if !triangles.len().is_multiple_of(9 * itemsize) {
            return Err("invalid triangle buffer length");
        }
        if normals.len() / normal_itemsize != triangles.len() / itemsize
            || !normals.len().is_multiple_of(normal_itemsize)
        {
            return Err("invalid normal buffer length");
        }
        if !lighting.iter().all(|v| v.is_finite()) || lighting[26] < 0.0 || lighting[27] < 0.0 {
            return Err("invalid lighting parameters");
        }
        if !triangles
            .chunks_exact(itemsize)
            .all(|v| number(v, itemsize).is_finite())
            || !normals
                .chunks_exact(normal_itemsize)
                .all(|v| number(v, normal_itemsize).is_finite())
        {
            return Err("mesh values must be finite");
        }
        let result = (|| {
            let (_, candidates) = visibility_into(
                triangles,
                itemsize,
                &mut self.depth,
                &mut self.winners,
                self.width,
                self.height,
            )?;
            for (pixel, face) in self.winners.iter().copied().enumerate() {
                if face == u32::MAX {
                    continue;
                }
                let mut record = [0; 24];
                record[..8].copy_from_slice(&(pixel as u64).to_ne_bytes());
                record[8..16].copy_from_slice(&(face as u64).to_ne_bytes());
                shading::shade_into(
                    &record,
                    triangles,
                    itemsize,
                    normals,
                    normal_itemsize,
                    self.width,
                    self.depth.len(),
                    &lighting,
                    &mut self.rgb[pixel * 3..pixel * 3 + 3],
                )?;
            }
            Ok(candidates)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }

    pub fn draw_flat(
        &mut self,
        triangles: &[u8],
        itemsize: usize,
        color: [u8; 3],
    ) -> Result<usize, &'static str> {
        self.check()?;
        if ![4, 8].contains(&itemsize) || !triangles.len().is_multiple_of(9 * itemsize) {
            return Err("invalid triangle buffer");
        }
        let result = (|| {
            let (_, candidates) = visibility_into(
                triangles,
                itemsize,
                &mut self.depth,
                &mut self.winners,
                self.width,
                self.height,
            )?;
            for (pixel, face) in self.winners.iter().copied().enumerate() {
                if face != u32::MAX {
                    self.rgb[pixel * 3..pixel * 3 + 3].copy_from_slice(&color);
                }
            }
            Ok(candidates)
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }

    pub fn rgba(&self) -> Result<Vec<u8>, &'static str> {
        self.check()?;
        let mut output = vec![0; self.depth.len() * 4];
        self.write_rgba(&mut output)?;
        Ok(output)
    }

    /// Fill the caller's final allocation without an intermediate image copy.
    pub fn write_rgba(&self, output: &mut [u8]) -> Result<(), &'static str> {
        self.check()?;
        if output.len() != self.depth.len() * 4 {
            return Err("invalid output buffer");
        }
        for (pixel, rgba) in output.as_chunks_mut::<4>().0.iter_mut().enumerate() {
            rgba[..3].copy_from_slice(&self.rgb[pixel * 3..pixel * 3 + 3]);
            rgba[3] = if self.depth[pixel] < f64::INFINITY {
                255
            } else {
                0
            };
        }
        Ok(())
    }

    pub fn output_bytes(&self) -> usize {
        self.depth.len() * 4
    }

    pub fn check(&self) -> Result<(), &'static str> {
        if self.failed {
            Err("failed frame cannot be published")
        } else {
            Ok(())
        }
    }
}
