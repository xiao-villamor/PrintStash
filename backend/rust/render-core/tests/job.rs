use printstash_render_core::job::{render, Profile, RenderOptions};

#[test]
fn encoded_preview_needs_no_python() {
    let vertices: Vec<u8> = [[0.0f32, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 0.0, 0.0]]
        .into_iter()
        .flatten()
        .flat_map(f32::to_ne_bytes)
        .collect();
    let faces: Vec<u8> = [0u64, 1, 2]
        .into_iter()
        .flat_map(u64::to_ne_bytes)
        .collect();
    let result = render(
        &vertices,
        &faces,
        &RenderOptions {
            width: 80,
            height: 60,
            chunk: 64,
            format: "WEBP",
            rotation: None,
            matte: false,
            profile: Profile {
                margin: 0.1,
                azimuth: -35.0,
                elevation: 18.0,
                flat_tilt: 25.0,
                flat_ratio: 0.35,
                albedo: [0.7, 0.75, 0.84],
                supersampling: [640, 2, 1],
            },
        },
    )
    .unwrap();
    let image = image::load_from_memory(&result.image).unwrap().into_rgba8();
    assert_eq!(image.dimensions(), (80, 60));
    assert_eq!(image.get_pixel(0, 0)[3], 0);
    assert!(image.pixels().filter(|p| p[3] == 255).count() > 100);
    assert!(result.seconds.iter().all(|v| v.is_finite() && *v >= 0.0));
}
