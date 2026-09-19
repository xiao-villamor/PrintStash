use printstash_render_core::Frame;

fn triangle(z: f32) -> Vec<u8> {
    [[0.0f32, 0.0, z], [4.0, 0.0, z], [0.0, 4.0, z]]
        .into_iter()
        .flatten()
        .flat_map(f32::to_ne_bytes)
        .collect()
}

#[test]
fn standalone_frame_renders_without_python() {
    let mut frame = Frame::new(4, 4).unwrap();
    frame.draw_flat(&triangle(0.0), 4, [20, 40, 60]).unwrap();
    let pixels = frame.rgba().unwrap();
    assert_eq!(&pixels[..4], &[20, 40, 60, 255]);
    assert_eq!(&pixels[60..], &[0, 0, 0, 0]);
}

#[test]
fn frame_preserves_first_depth_tie() {
    let mut frame = Frame::new(4, 4).unwrap();
    frame.draw_flat(&triangle(0.0), 4, [20, 40, 60]).unwrap();
    frame.draw_flat(&triangle(0.0), 4, [80, 90, 100]).unwrap();
    assert_eq!(&frame.rgba().unwrap()[..4], &[20, 40, 60, 255]);
}

#[test]
fn frame_rejects_invalid_dimensions() {
    for (width, height) in [(0, 4), (4, 0), (usize::MAX, 2), (4097, 4096)] {
        assert!(Frame::new(width, height).is_err());
    }
}

#[test]
fn failed_frame_cannot_publish() {
    let mut frame = Frame::new(4, 4).unwrap();
    assert!(frame.draw_flat(&triangle(f32::NAN), 4, [0; 3]).is_err());
    assert!(frame.rgba().is_err());
}

#[test]
fn frame_rejects_bad_normal_buffer() {
    let mut frame = Frame::new(4, 4).unwrap();
    assert!(frame
        .draw_phong(&triangle(0.0), 4, &[0], 4, [0.0; 31])
        .is_err());
}

#[test]
fn prepared_mesh_renders_without_python() {
    let vertices = triangle(0.0);
    let faces: Vec<u8> = [0_u64, 1, 2]
        .into_iter()
        .flat_map(u64::to_ne_bytes)
        .collect();
    let mesh = printstash_render_core::PreparedPreview::new(&vertices, &faces, 1).unwrap();
    drop(vertices);
    drop(faces);
    let pixels = mesh
        .render_frame(
            [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
            1.0,
            8,
            8,
            0.1,
            [0.0; 31],
            [20, 40, 60],
        )
        .unwrap()
        .rgba()
        .unwrap();
    assert!(pixels
        .as_chunks::<4>()
        .0
        .iter()
        .any(|p| p == &[20, 40, 60, 255]));
    assert!(pixels.as_chunks::<4>().0.iter().any(|p| p[3] == 0));
}

#[test]
fn prepared_mesh_rejects_invalid_indices() {
    let faces: Vec<u8> = [0_u64, 1, 3]
        .into_iter()
        .flat_map(u64::to_ne_bytes)
        .collect();
    assert!(printstash_render_core::PreparedPreview::new(&triangle(0.0), &faces, 1).is_err());
}
