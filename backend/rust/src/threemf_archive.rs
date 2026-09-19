//! Native ZIP input for the scene loader's already validated package members.
//! One open archive per scene; no Python callbacks while inflating or parsing.
use crate::threemf::{pack, parse, ParsedModel};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::fs::File;
use std::io::{self, BufReader, Read};
use std::path::PathBuf;
use zip::ZipArchive;

struct LimitedReader<R> {
    inner: R,
    remaining: u64,
}

impl<R: Read> Read for LimitedReader<R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let requested = buffer
            .len()
            .min(65_536)
            .min(usize::try_from(self.remaining.saturating_add(1)).unwrap_or(usize::MAX));
        let count = self.inner.read(&mut buffer[..requested])?;
        if count as u64 > self.remaining {
            return Err(io::Error::other("3MF XML input limit exceeded"));
        }
        self.remaining -= count as u64;
        Ok(count)
    }
}

#[pyclass(module = "printstash_mesh_native")]
pub struct ThreeMfArchive {
    archive: Option<ZipArchive<BufReader<File>>>,
}

impl ThreeMfArchive {
    fn read_part_data(
        &mut self,
        name: &str,
        expected_size: u64,
        expected_crc: u32,
        max_bytes: u64,
    ) -> Result<(Vec<u8>, Vec<crate::threemf::Mesh>), String> {
        let archive = self.archive.as_mut().ok_or("3MF archive is closed")?;
        let entry = archive.by_name(name).map_err(|e| e.to_string())?;
        if entry.size() != expected_size || entry.crc32() != expected_crc {
            return Err("3MF package member changed".into());
        }
        if entry.size() > max_bytes {
            return Err("3MF XML input limit exceeded".into());
        }
        let mut source = LimitedReader {
            inner: entry,
            remaining: expected_size,
        };
        let result = parse(&mut source)?;
        if source.remaining != 0 {
            return Err("3MF XML member size mismatch".into());
        }
        Ok(result)
    }
}

#[pymethods]
impl ThreeMfArchive {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let archive = py.detach(|| {
            let file = File::open(path).map_err(|e| e.to_string())?;
            ZipArchive::new(BufReader::with_capacity(65_536, file)).map_err(|e| e.to_string())
        });
        Ok(Self {
            archive: Some(archive.map_err(PyValueError::new_err)?),
        })
    }

    /// Expected size and CRC bind this read to the validated package inventory.
    /// Read through EOF to check CRC; a short/truncated member is never accepted.
    fn read_part<'py>(
        &mut self,
        py: Python<'py>,
        name: &str,
        expected_size: u64,
        expected_crc: u32,
        max_bytes: u64,
    ) -> PyResult<ParsedModel<'py>> {
        let (shell, meshes) = py
            .detach(|| self.read_part_data(name, expected_size, expected_crc, max_bytes))
            .map_err(PyValueError::new_err)?;
        pack(py, shell, meshes)
    }

    /// Keep geometry in native resource handles while Python resolves the
    /// small component graph and transform list.
    fn read_part_native<'py>(
        &mut self,
        py: Python<'py>,
        name: &str,
        expected_size: u64,
        expected_crc: u32,
        max_bytes: u64,
    ) -> PyResult<(
        Bound<'py, PyBytes>,
        Vec<Py<crate::native_scene::NativeMeshResource>>,
    )> {
        let (shell, meshes) = py
            .detach(|| self.read_part_data(name, expected_size, expected_crc, max_bytes))
            .map_err(PyValueError::new_err)?;
        Ok((
            PyBytes::new(py, &shell),
            crate::native_scene::resources(py, meshes)?,
        ))
    }

    fn close(&mut self) {
        self.archive = None;
    }
}
