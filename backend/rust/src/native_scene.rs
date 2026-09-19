//! Native 3MF resource handles and an owned transformed preview scene.
use crate::threemf::Mesh;
use printstash_render_core::job::{self, Profile, RenderOptions};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::sync::Arc;

#[pyclass(module = "printstash_mesh_native", frozen)]
pub struct NativeMeshResource {
    mesh: Arc<Mesh>,
}

pub(crate) fn resources(
    py: Python<'_>,
    meshes: Vec<Mesh>,
) -> PyResult<Vec<Py<NativeMeshResource>>> {
    meshes
        .into_iter()
        .map(|mesh| {
            Py::new(
                py,
                NativeMeshResource {
                    mesh: Arc::new(mesh),
                },
            )
        })
        .collect()
}

#[pymethods]
impl NativeMeshResource {
    fn buffers<'py>(
        &self,
        py: Python<'py>,
    ) -> PyResult<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)> {
        let vertices = PyBytes::new_with(py, self.mesh.vertices.len() * 24, |output| {
            py.detach(|| {
                for (value, target) in self
                    .mesh
                    .vertices
                    .iter()
                    .flatten()
                    .zip(output.as_chunks_mut::<8>().0.iter_mut())
                {
                    target.copy_from_slice(&value.to_ne_bytes());
                }
            });
            Ok(())
        })?;
        let faces = PyBytes::new_with(py, self.mesh.faces.len() * 24, |output| {
            py.detach(|| {
                for (value, target) in self
                    .mesh
                    .faces
                    .iter()
                    .flatten()
                    .zip(output.as_chunks_mut::<8>().0.iter_mut())
                {
                    target.copy_from_slice(&value.to_ne_bytes());
                }
            });
            Ok(())
        })?;
        Ok((vertices, faces))
    }

    #[getter]
    fn triangle_count(&self) -> usize {
        self.mesh.faces.len()
    }
}

#[pyclass(module = "printstash_mesh_native", frozen)]
pub struct NativeScenePreview {
    preview: printstash_render_core::PreparedPreview,
}

#[pymethods]
impl NativeScenePreview {
    #[new]
    #[pyo3(signature = (instances, max_faces=2_000_000, chunk=64_000))]
    fn new(
        py: Python<'_>,
        instances: Vec<(Py<NativeMeshResource>, [[f64; 4]; 4])>,
        max_faces: usize,
        chunk: usize,
    ) -> PyResult<Self> {
        if instances.is_empty()
            || instances.len() > 10_000
            || !(1..=2_000_000).contains(&max_faces)
            || !(1..=2_000_000).contains(&chunk)
        {
            return Err(PyValueError::new_err("invalid native scene budget"));
        }
        let instances = instances
            .into_iter()
            .map(|(resource, transform)| (resource.borrow(py).mesh.clone(), transform))
            .collect::<Vec<_>>();
        let preview = py
            .detach(|| {
                let faces = instances.iter().try_fold(0usize, |total, (resource, _)| {
                    total
                        .checked_add(resource.faces.len())
                        .ok_or("scene overflow")
                })?;
                if faces == 0 || faces > max_faces {
                    return Err("native scene face budget");
                }
                let vertices = instances.iter().try_fold(0usize, |total, (resource, _)| {
                    total
                        .checked_add(resource.vertices.len())
                        .ok_or("scene overflow")
                })?;
                if vertices == 0 || vertices > 6_000_000 {
                    return Err("native scene vertex budget");
                }
                let mut points = Vec::with_capacity(vertices);
                let mut indices = Vec::with_capacity(faces);
                for (resource, transform) in instances {
                    if !transform.iter().flatten().all(|value| value.is_finite())
                        || transform[3] != [0.0, 0.0, 0.0, 1.0]
                    {
                        return Err("invalid native scene transform");
                    }
                    let offset = u32::try_from(points.len()).map_err(|_| "scene overflow")?;
                    for vertex in &resource.vertices {
                        let transformed: [f64; 3] = std::array::from_fn(|axis| {
                            (transform[axis][0] * vertex[0] + transform[axis][1] * vertex[1])
                                + transform[axis][2] * vertex[2]
                                + transform[axis][3]
                        });
                        if !transformed.iter().all(|value| value.is_finite())
                            || transformed
                                .iter()
                                .any(|value| value.abs() > f32::MAX as f64)
                        {
                            return Err("invalid transformed coordinate");
                        }
                        points.push(transformed.map(|value| value as f32));
                    }
                    for face in &resource.faces {
                        let face =
                            face.map(|value| u32::try_from(value).map_err(|_| "scene overflow"));
                        let [a, b, c] = face;
                        indices.push([
                            a?.checked_add(offset).ok_or("scene overflow")?,
                            b?.checked_add(offset).ok_or("scene overflow")?,
                            c?.checked_add(offset).ok_or("scene overflow")?,
                        ]);
                    }
                }
                printstash_render_core::PreparedPreview::from_parts(points, indices, chunk)
            })
            .map_err(PyValueError::new_err)?;
        Ok(Self { preview })
    }

    #[pyo3(signature = (width, height, format, recipe, supersampling, rotation=None, matte=false))]
    #[allow(clippy::too_many_arguments)]
    fn render_preview<'py>(
        &self,
        py: Python<'py>,
        width: usize,
        height: usize,
        format: &str,
        recipe: [f64; 8],
        supersampling: [usize; 3],
        rotation: Option<[[f64; 3]; 3]>,
        matte: bool,
    ) -> PyResult<(Bound<'py, PyBytes>, [f64; 5])> {
        let result = py
            .detach(|| {
                job::render_prepared(
                    &self.preview,
                    &RenderOptions {
                        width,
                        height,
                        chunk: 64_000,
                        format,
                        rotation,
                        matte,
                        profile: Profile {
                            margin: recipe[0],
                            azimuth: recipe[1],
                            elevation: recipe[2],
                            flat_tilt: recipe[3],
                            flat_ratio: recipe[4],
                            albedo: [recipe[5], recipe[6], recipe[7]],
                            supersampling,
                        },
                    },
                )
            })
            .map_err(PyValueError::new_err)?;
        Ok((PyBytes::new(py, &result.image), result.seconds))
    }
}
