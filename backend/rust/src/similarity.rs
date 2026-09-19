//! PyO3 translation boundary for the framework-free similarity core.
use printstash_similarity_core::{
    self as core, Face, Mesh, Point, Transform, Triangle, VerificationMetrics,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;

fn error(error: core::Error) -> PyErr {
    PyValueError::new_err(error.code())
}

pub(crate) fn points(bytes: &[u8]) -> PyResult<Vec<Point>> {
    if !bytes.len().is_multiple_of(24) {
        return Err(PyValueError::new_err("invalid_vertices"));
    }
    Ok(bytes
        .as_chunks::<24>()
        .0
        .iter()
        .map(|chunk| {
            std::array::from_fn(|axis| {
                f64::from_ne_bytes(chunk[axis * 8..axis * 8 + 8].try_into().unwrap())
            })
        })
        .collect())
}

fn faces(bytes: &[u8]) -> PyResult<Vec<Face>> {
    if !bytes.len().is_multiple_of(24) {
        return Err(PyValueError::new_err("invalid_faces"));
    }
    bytes
        .as_chunks::<24>()
        .0
        .iter()
        .map(|chunk| {
            let mut face = [0usize; 3];
            for axis in 0..3 {
                let value = i64::from_ne_bytes(chunk[axis * 8..axis * 8 + 8].try_into().unwrap());
                if value < 0 {
                    return Err(PyValueError::new_err("invalid_faces"));
                }
                face[axis] = value as usize;
            }
            Ok(face)
        })
        .collect()
}

fn mesh(vertices: &[u8], face_bytes: &[u8]) -> PyResult<Mesh> {
    Mesh::try_new(points(vertices)?, faces(face_bytes)?).map_err(error)
}

pub(crate) fn triangles(bytes: &[u8]) -> PyResult<Vec<Triangle>> {
    if !bytes.len().is_multiple_of(72) {
        return Err(PyValueError::new_err("invalid_proximity_surface"));
    }
    Ok(bytes
        .as_chunks::<72>()
        .0
        .iter()
        .map(|chunk| {
            [
                std::array::from_fn(|axis| {
                    f64::from_ne_bytes(chunk[axis * 8..axis * 8 + 8].try_into().unwrap())
                }),
                std::array::from_fn(|axis| {
                    f64::from_ne_bytes(chunk[24 + axis * 8..32 + axis * 8].try_into().unwrap())
                }),
                std::array::from_fn(|axis| {
                    f64::from_ne_bytes(chunk[48 + axis * 8..56 + axis * 8].try_into().unwrap())
                }),
            ]
        })
        .collect())
}

#[pyfunction]
pub fn nearest_neighbors<'py>(
    py: Python<'py>,
    source: &[u8],
    target: &[u8],
) -> PyResult<Bound<'py, PyBytes>> {
    let source = points(source)?;
    let target = points(target)?;
    let result = py
        .detach(|| core::nearest_neighbors(&source, &target))
        .map_err(error)?;
    let mut output = Vec::with_capacity(result.len() * 16);
    for (distance, index) in result {
        output.extend_from_slice(&distance.to_ne_bytes());
        output.extend_from_slice(&(index as u64).to_ne_bytes());
    }
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn equivalent_triangles(
    py: Python<'_>,
    left_vertices: &[u8],
    left_faces: &[u8],
    right_vertices: &[u8],
    right_faces: &[u8],
    rotation: [f64; 9],
    scale: f64,
    translation: Point,
    tolerance: f64,
) -> PyResult<bool> {
    let left = mesh(left_vertices, left_faces)?;
    let right = mesh(right_vertices, right_faces)?;
    py.detach(|| {
        core::equivalent_triangles(
            &left,
            &right,
            Transform {
                rotation,
                scale,
                translation,
            },
            tolerance,
        )
    })
    .map_err(error)
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn verification_decision(
    exact: bool,
    reflected: bool,
    mirror_ambiguous: bool,
    factor: f64,
    surface_chamfer: f64,
    surface_hausdorff: f64,
    voxel_iou: Option<f64>,
    left_watertight: bool,
    right_watertight: bool,
    left_euler: i64,
    right_euler: i64,
    left_faces: usize,
    right_faces: usize,
    sampled_chamfer: f64,
    sampled_hausdorff: f64,
) -> PyResult<(Option<&'static str>, f64)> {
    let decision = core::verification_decision(VerificationMetrics {
        exact,
        reflected,
        mirror_ambiguous,
        factor,
        surface_chamfer,
        surface_hausdorff,
        voxel_iou,
        left_watertight,
        right_watertight,
        left_euler,
        right_euler,
        left_faces,
        right_faces,
        sampled_chamfer,
        sampled_hausdorff,
    })
    .map_err(error)?;
    Ok((
        decision.evidence.map(|value| value.code()),
        decision.confidence,
    ))
}

#[pyfunction]
pub fn voxelize<'py>(
    py: Python<'py>,
    vertices: &[u8],
    faces: &[u8],
    half_width: f64,
    resolution: usize,
    fill: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let mesh = mesh(vertices, faces)?;
    let result = py
        .detach(|| core::voxelize(&mesh, half_width, resolution, fill))
        .map_err(error)?;
    Ok(PyBytes::new(py, &result))
}

#[pyfunction]
pub fn sh_spectrum<'py>(
    py: Python<'py>,
    vertices: &[u8],
    faces: &[u8],
    fill: bool,
) -> PyResult<Bound<'py, PyBytes>> {
    let mesh = mesh(vertices, faces)?;
    let values = py
        .detach(|| core::sh_spectrum(&mesh, fill))
        .map_err(error)?;
    let mut output = Vec::with_capacity(values.len() * 8);
    for value in values {
        output.extend_from_slice(&value.to_ne_bytes());
    }
    Ok(PyBytes::new(py, &output))
}

#[pyfunction]
pub fn dct_hash<'py>(py: Python<'py>, pixels: &[u8]) -> PyResult<Bound<'py, PyBytes>> {
    if !pixels.len().is_multiple_of(8) {
        return Err(PyValueError::new_err("invalid_view_image"));
    }
    let values: Vec<f64> = pixels
        .as_chunks::<8>()
        .0
        .iter()
        .map(|bytes| f64::from_ne_bytes(*bytes))
        .collect();
    let hash = py.detach(|| core::dct_hash(&values)).map_err(error)?;
    Ok(PyBytes::new(py, &hash))
}

#[pyfunction]
pub fn volume_inertia_ratios(py: Python<'_>, triangle_bytes: &[u8]) -> PyResult<(f64, f64, f64)> {
    let triangles =
        triangles(triangle_bytes).map_err(|_| PyValueError::new_err("invalid_inertia_geometry"))?;
    py.detach(|| core::volume_inertia_ratios(&triangles))
        .map_err(error)
}
