//! Stream binary STL coordinates directly into final immutable numeric buffers.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::fs::File;
use std::io::{BufReader, Read};
use std::path::PathBuf;

type MeshBuffers<'py> = Option<(Bound<'py, PyBytes>, Bound<'py, PyBytes>)>;

#[pyfunction]
#[pyo3(signature = (path, max_bytes=536_870_912))]
pub fn load_binary_stl<'py>(
    py: Python<'py>,
    path: PathBuf,
    max_bytes: u64,
) -> PyResult<MeshBuffers<'py>> {
    let mut file = File::open(path)?;
    let length = file.metadata()?.len();
    if length < 84 {
        return Ok(None);
    }
    let mut header = [0_u8; 84];
    file.read_exact(&mut header)?;
    let faces = u32::from_le_bytes(header[80..84].try_into().unwrap()) as u64;
    if length != 84 + faces * 50 {
        return Ok(None);
    }
    let expanded = faces * 96;
    if length > max_bytes || expanded > max_bytes || expanded > usize::MAX as u64 {
        return Err(PyValueError::new_err("binary STL geometry limit exceeded"));
    }
    let faces = faces as usize;
    let mut reader = BufReader::with_capacity(65_536, file);
    let vertices = PyBytes::new_with(py, faces * 72, |output| {
        py.detach(|| -> PyResult<()> {
            let mut record = [0_u8; 50];
            for triangle in output.chunks_exact_mut(72) {
                reader.read_exact(&mut record)?;
                for (source, target) in record[12..48]
                    .chunks_exact(4)
                    .zip(triangle.chunks_exact_mut(8))
                {
                    let coordinate = f32::from_le_bytes(source.try_into().unwrap()) as f64;
                    if !coordinate.is_finite() {
                        return Err(PyValueError::new_err("STL coordinates must be finite"));
                    }
                    target.copy_from_slice(&coordinate.to_ne_bytes());
                }
            }
            Ok(())
        })
    })?;
    let indices = PyBytes::new_with(py, faces * 24, |output| {
        py.detach(|| {
            for (index, target) in output.chunks_exact_mut(8).enumerate() {
                target.copy_from_slice(&(index as u64).to_ne_bytes());
            }
        });
        Ok(())
    })?;
    Ok(Some((vertices, indices)))
}
