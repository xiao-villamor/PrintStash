use printstash_render_core::stl_source::NativeStlSource;
use std::{
    fs,
    path::PathBuf,
    sync::atomic::{AtomicU64, Ordering},
};
static NEXT: AtomicU64 = AtomicU64::new(0);
struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let path = std::env::temp_dir().join(format!(
            "printstash-source-{}-{}.stl",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        fs::write(&path, b"solid x\nfacet normal 0 0 1\nouter loop\nvertex 0 0 0\nvertex 1 0 0\nvertex 0 1 0\nendloop\nendfacet\nendsolid x\n").unwrap();
        Self(path)
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}

#[test]
fn ascii_pass_accepts_complete_facets() {
    let fixture = Fixture::new();
    let mut source = NativeStlSource::new(fixture.0.clone(), 100, 10000, 64, 10.0).unwrap();
    let summary = source.analyze().unwrap();
    assert_eq!(summary.0, 1);
    assert_eq!(summary.2, [0.0, 0.0, 0.0]);
    assert_eq!(summary.3, [1.0, 1.0, 0.0]);
}

#[test]
fn changed_source_cannot_render() {
    let fixture = Fixture::new();
    let mut source = NativeStlSource::new(fixture.0.clone(), 100, 10000, 64, 10.0).unwrap();
    source.analyze().unwrap();
    fs::write(&fixture.0, b"changed").unwrap();
    let result = source.render_depth(
        80,
        60,
        10000,
        [0.0; 3],
        [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        [-1.0; 3],
        [2.0; 3],
        [0.0; 3],
        1.0,
    );
    assert!(result.err().unwrap().contains("source changed"));
    assert!(source.analyze().err().unwrap().contains("failed"));
}
