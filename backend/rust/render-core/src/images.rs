//! Bounded image postprocessing and lossless codecs, detached from Python.
use fast_image_resize::{
    images::{Image, ImageRef},
    FilterType, PixelType, ResizeAlg, ResizeOptions, Resizer,
};
use image::{
    codecs::{
        png::{CompressionType, FilterType as PngFilter, PngEncoder},
        webp::WebPEncoder,
    },
    ExtendedColorType, ImageEncoder,
};

fn pixels(width: u32, height: u32) -> Result<usize, &'static str> {
    if width == 0 || height == 0 || width > 4096 || height > 4096 {
        return Err("invalid image dimensions");
    }
    Ok(width as usize * height as usize)
}

// Keep the reference horizontal-then-vertical pass order. Reversing the
// convolution passes changes clipped Lanczos edge pixels, especially alpha.
fn resize_rgba(
    rgba: &[u8],
    sw: u32,
    sh: u32,
    width: u32,
    height: u32,
    filter: FilterType,
    alpha: bool,
) -> Result<Vec<u8>, String> {
    let mut current = rgba.to_vec();
    if alpha {
        for p in current.chunks_exact_mut(4) {
            let a = p[3] as u32;
            for c in &mut p[..3] {
                let t = *c as u32 * a + 128;
                *c = ((t + (t >> 8)) >> 8) as u8;
            }
        }
    }
    let options = ResizeOptions::new()
        .resize_alg(ResizeAlg::Convolution(filter))
        .use_alpha(false);
    let mut resizer = Resizer::new();
    if sw != width {
        let source = ImageRef::new(sw, sh, &current, PixelType::U8x4).map_err(|e| e.to_string())?;
        let mut destination = Image::new(width, sh, PixelType::U8x4);
        resizer
            .resize(&source, &mut destination, &options)
            .map_err(|e| e.to_string())?;
        current = destination.into_vec();
    }
    if sh != height {
        let source =
            ImageRef::new(width, sh, &current, PixelType::U8x4).map_err(|e| e.to_string())?;
        let mut destination = Image::new(width, height, PixelType::U8x4);
        resizer
            .resize(&source, &mut destination, &options)
            .map_err(|e| e.to_string())?;
        current = destination.into_vec();
    }
    if alpha {
        for p in current.chunks_exact_mut(4) {
            let a = p[3] as u32;
            for c in &mut p[..3] {
                *c = if a == 0 {
                    0
                } else {
                    (255 * (*c as u32) / a).min(255) as u8
                };
            }
        }
    }
    Ok(current)
}

#[allow(clippy::too_many_arguments)]
pub fn process_image(
    rgba: &[u8],
    source_width: u32,
    source_height: u32,
    width: u32,
    height: u32,
    filter: &str,
    alpha: bool,
    vignette: bool,
    format: &str,
) -> Result<Vec<u8>, String> {
    let count = pixels(source_width, source_height)?;
    pixels(width, height)?;
    if rgba.len() != count * 4 || !matches!(format, "PNG" | "WEBP" | "RGB") {
        return Err("invalid image buffer or format".into());
    }
    let filter = match filter {
        "lanczos" => FilterType::Lanczos3,
        "bilinear" => FilterType::Bilinear,
        _ => return Err("invalid resize filter".into()),
    };
    let output = (|| -> Result<Vec<u8>, String> {
        let mut data = if (source_width, source_height) == (width, height) {
            rgba.to_vec()
        } else {
            resize_rgba(
                rgba,
                source_width,
                source_height,
                width,
                height,
                filter,
                alpha,
            )?
        };
        if vignette {
            for (i, p) in data.chunks_exact_mut(4).enumerate() {
                let x = if width == 1 {
                    -1.0
                } else {
                    (-1.0 + 2.0 * (i % width as usize) as f64 / (width - 1) as f64) as f32
                };
                let y = if height == 1 {
                    -1.0
                } else {
                    (-1.0 + 2.0 * (i / width as usize) as f64 / (height - 1) as f64) as f32
                };
                let factor = 1.0 - 0.18 * (x * x + y * y).clamp(0.0, 1.0);
                for c in &mut p[..3] {
                    *c = (*c as f32 * factor).clamp(0.0, 255.0) as u8;
                }
            }
        }
        if format == "RGB" {
            return Ok(data
                .chunks_exact(4)
                .flat_map(|p| {
                    let alpha = p[3] as u32;
                    std::array::from_fn::<_, 3, _>(|a| {
                        let value = p[a] as u32 * alpha + 255 * (255 - alpha) + 128;
                        ((value + (value >> 8)) >> 8) as u8
                    })
                })
                .collect());
        }
        let mut out = Vec::new();
        if format == "PNG" {
            PngEncoder::new_with_quality(&mut out, CompressionType::Default, PngFilter::Adaptive)
                .write_image(&data, width, height, ExtendedColorType::Rgba8)
                .map_err(|e| e.to_string())?;
        } else {
            WebPEncoder::new_lossless(&mut out)
                .write_image(&data, width, height, ExtendedColorType::Rgba8)
                .map_err(|e| e.to_string())?;
        }
        Ok(out)
    })()?;
    Ok(output)
}

fn normalize(v: [f32; 3]) -> [f32; 3] {
    let length = ((v[0] * v[0] + v[1] * v[1]) + v[2] * v[2]).sqrt().max(1e-6);
    v.map(|c| c / length)
}

pub fn shade_depth(
    depth: &[u8],
    width: u32,
    height: u32,
    scale: f64,
    albedo: [f32; 3],
) -> Result<Vec<u8>, String> {
    let count = pixels(width, height)?;
    if depth.len() != count * 4
        || !scale.is_finite()
        || scale <= 0.0
        || !albedo
            .iter()
            .all(|v| v.is_finite() && (0.0..=1.0).contains(v))
    {
        return Err("invalid depth image".into());
    }
    let data = {
        let z: Vec<f32> = depth
            .chunks_exact(4)
            .map(|p| f32::from_ne_bytes(p.try_into().unwrap()))
            .collect();
        let w = width as usize;
        let h = height as usize;
        let mut normals = vec![[0.0f32; 3]; count];
        for i in 0..count {
            if !z[i].is_finite() {
                continue;
            }
            let x = i % w;
            let y = i / w;
            let value = |x: usize, y: usize| {
                let v = z[y * w + x];
                v.is_finite().then_some(v)
            };
            let slope = |a: Option<f32>, b: Option<f32>| match (a, b) {
                (Some(a), Some(b)) => (b - a) * 0.5,
                (None, Some(b)) => b - z[i],
                (Some(a), None) => z[i] - a,
                _ => 0.0,
            };
            let dx = slope(
                x.checked_sub(1).and_then(|x| value(x, y)),
                (x + 1 < w).then(|| value(x + 1, y)).flatten(),
            );
            let dy = slope(
                y.checked_sub(1).and_then(|y| value(x, y)),
                (y + 1 < h).then(|| value(x, y + 1)).flatten(),
            );
            normals[i] = normalize([
                -((dx as f64 * scale) as f32).clamp(-8.0, 8.0),
                ((dy as f64 * scale) as f32).clamp(-8.0, 8.0),
                1.0,
            ]);
        }
        let light = normalize([-0.45, 0.6, 1.0]);
        let mut rgba = vec![0u8; count * 4];
        for i in 0..count {
            if !z[i].is_finite() {
                continue;
            }
            let x = i % w;
            let y = i / w;
            let mut sum = [0.0; 3];
            let mut support = 0.0;
            // Match the reference roll order; retain holes and ignore edges.
            for dy in -1isize..=1 {
                for dx in -1isize..=1 {
                    let nx = x as isize - dx;
                    let ny = y as isize - dy;
                    if nx >= 0 && ny >= 0 && nx < w as isize && ny < h as isize {
                        let j = ny as usize * w + nx as usize;
                        if z[j].is_finite() {
                            for a in 0..3 {
                                sum[a] += normals[j][a];
                            }
                            support += 1.0;
                        }
                    }
                }
            }
            let n = normalize(sum.map(|v| v / support));
            let diffuse = ((n[0] * light[0] + n[1] * light[1]) + n[2] * light[2]).clamp(0.0, 1.0);
            let brightness = 0.30 + diffuse * 0.70;
            let rim = (1.0 - n[2].clamp(0.0, 1.0)).powi(2) * 18.0;
            for a in 0..3 {
                rgba[i * 4 + a] = (albedo[a] * 255.0 * brightness + rim).clamp(0.0, 255.0) as u8;
            }
            rgba[i * 4 + 3] = 255;
        }
        rgba
    };
    Ok(data)
}

/// Decode and normalize supported thumbnails before the storage boundary.
pub fn normalize_thumbnail(
    data: &[u8],
    width: u32,
    normalize: bool,
    margin: f64,
) -> Result<Option<Vec<u8>>, String> {
    use image::{ImageDecoder, ImageFormat, ImageReader};
    use std::io::Cursor;
    if !(320..=1280).contains(&width) || !margin.is_finite() || !(0.0..0.5).contains(&margin) {
        return Err("thumbnail_width_invalid".into());
    }
    let format = match image::guess_format(data) {
        Ok(f @ (ImageFormat::Png | ImageFormat::WebP | ImageFormat::Jpeg)) => f,
        _ => return Ok(None), // Other legacy image formats retain their decoder.
    };
    let result = (|| -> Result<Vec<u8>, String> {
        let mut reader = ImageReader::with_format(Cursor::new(data), format);
        let mut limits = image::Limits::default();
        limits.max_alloc = Some(256 * 1024 * 1024);
        reader.limits(limits);
        let decoder = reader.into_decoder().map_err(|e| e.to_string())?;
        let (sw, sh) = decoder.dimensions();
        if sw == 0 || sh == 0 || sw as u64 * sh as u64 > 25_000_000 {
            return Err("thumbnail_too_large".into());
        }
        let source_alpha = decoder.color_type().has_alpha();
        let source = image::DynamicImage::from_decoder(decoder)
            .map_err(|e| e.to_string())?
            .into_rgba8();
        let height = (width as f64 * 0.75).round_ties_even() as u32;
        let mut left = sw;
        let mut top = sh;
        let mut right = 0;
        let mut bottom = 0;
        for (x, y, p) in source.enumerate_pixels() {
            if p[3] > 0 {
                left = left.min(x);
                top = top.min(y);
                right = right.max(x + 1);
                bottom = bottom.max(y + 1);
            }
        }
        if right == 0 {
            return Err("thumbnail_empty".into());
        }
        let ratio =
            ((right - left) as f64 / width as f64).max((bottom - top) as f64 / height as f64);
        let canonical = source_alpha
            && (sw, sh) == (width, height)
            && left > 0
            && top > 0
            && right < width
            && bottom < height
            && (0.76..=0.84).contains(&ratio);
        if normalize && canonical && format == ImageFormat::WebP {
            return Ok(data.to_vec());
        }
        let (pixels, ow, oh) = if normalize && canonical {
            (source.into_raw(), width, height)
        } else {
            let crop = if normalize {
                image::imageops::crop_imm(&source, left, top, right - left, bottom - top).to_image()
            } else {
                source
            };
            let (cw, ch) = crop.dimensions();
            let (maxw, maxh) = if normalize {
                (
                    (width as f64 * (1.0 - 2.0 * margin))
                        .round_ties_even()
                        .max(1.0),
                    (height as f64 * (1.0 - 2.0 * margin))
                        .round_ties_even()
                        .max(1.0),
                )
            } else {
                (width as f64, height as f64)
            };
            let mut scale = (maxw / cw as f64).min(maxh / ch as f64);
            if !normalize {
                scale = scale.min(1.0);
            }
            let rw = (cw as f64 * scale).round_ties_even().max(1.0) as u32;
            let rh = (ch as f64 * scale).round_ties_even().max(1.0) as u32;
            let resized = if (cw, ch) == (rw, rh) {
                crop.into_raw()
            } else {
                resize_rgba(crop.as_raw(), cw, ch, rw, rh, FilterType::Lanczos3, true)?
            };
            if normalize {
                let mut canvas = vec![0u8; width as usize * height as usize * 4];
                let x = (width - rw) / 2;
                let y = (height - rh) / 2;
                for row in 0..rh as usize {
                    for col in 0..rw as usize {
                        let from = (row * rw as usize + col) * 4;
                        let to = ((row + y as usize) * width as usize + col + x as usize) * 4;
                        if resized[from + 3] > 0 {
                            canvas[to..to + 4].copy_from_slice(&resized[from..from + 4]);
                        }
                    }
                }
                (canvas, width, height)
            } else {
                (resized, rw, rh)
            }
        };
        let mut output = Vec::new();
        WebPEncoder::new_lossless(&mut output)
            .write_image(&pixels, ow, oh, ExtendedColorType::Rgba8)
            .map_err(|e| e.to_string())?;
        Ok(output)
    })()?;
    Ok(Some(result))
}
