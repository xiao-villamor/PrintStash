//! Narrow Python boundary for the framework-neutral archive core.

use printstash_archive_core::{
    self as core, ArchiveError, ArchiveLimits, ArchiveReader, SelectedEntry,
};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::path::PathBuf;

type PythonArchiveEntry = (String, String, u64, Option<String>, bool);

fn python_error(error: ArchiveError) -> PyErr {
    match error.code() {
        Some(code) => PyValueError::new_err(code),
        None => PyOSError::new_err(error.to_string()),
    }
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn inspect_archive(
    py: Python<'_>,
    path: PathBuf,
    max_entries: usize,
    max_entry_bytes: u64,
    max_total_bytes: u64,
    max_central_directory_bytes: u64,
    max_path_bytes: usize,
    max_depth: usize,
    file_types: HashMap<String, String>,
    image_suffixes: HashSet<String>,
) -> PyResult<Vec<PythonArchiveEntry>> {
    py.detach(|| {
        core::inspect_archive(
            &path,
            ArchiveLimits {
                max_entries,
                max_entry_bytes,
                max_total_bytes,
                max_central_directory_bytes,
                max_path_bytes,
                max_depth,
            },
            &file_types,
            &image_suffixes,
        )
        .map(|entries| {
            entries
                .into_iter()
                .map(|entry| {
                    (
                        entry.entry_id(),
                        entry.name,
                        entry.size_bytes,
                        entry.file_type,
                        entry.is_image,
                    )
                })
                .collect()
        })
    })
    .map_err(python_error)
}

#[pyfunction]
pub fn safe_entry_name(name: &str) -> bool {
    core::safe_entry_name(name)
}

#[pyfunction]
pub fn safe_subdir(name: &str) -> String {
    core::safe_subdir(name)
}

#[pyclass(module = "printstash_mesh_native")]
pub struct NativeArchive {
    reader: Option<ArchiveReader>,
}

fn selected_tuple(entry: SelectedEntry) -> (usize, String, String) {
    (entry.index, entry.suffix, entry.name)
}

#[pymethods]
impl NativeArchive {
    #[new]
    fn new(py: Python<'_>, path: PathBuf) -> PyResult<Self> {
        let reader = py
            .detach(|| ArchiveReader::open(&path))
            .map_err(python_error)?;
        Ok(Self {
            reader: Some(reader),
        })
    }

    fn selected_entries(
        &mut self,
        py: Python<'_>,
        names: Vec<String>,
        max_entry_bytes: u64,
        importable_suffixes: HashSet<String>,
    ) -> PyResult<Vec<(usize, String, String)>> {
        let reader = self
            .reader
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("archive is closed"))?;
        py.detach(|| reader.selected_entries(&names, max_entry_bytes, &importable_suffixes))
            .map(|entries| entries.into_iter().map(selected_tuple).collect())
            .map_err(python_error)
    }

    fn selected_entry(
        &mut self,
        py: Python<'_>,
        name: &str,
        max_entry_bytes: u64,
        importable_suffixes: HashSet<String>,
    ) -> PyResult<Option<(usize, String, String)>> {
        let reader = self
            .reader
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("archive is closed"))?;
        py.detach(|| reader.selected_entry(name, max_entry_bytes, &importable_suffixes))
            .map(|entry| entry.map(selected_tuple))
            .map_err(python_error)
    }

    fn extract_to(
        &mut self,
        py: Python<'_>,
        index: usize,
        destination: PathBuf,
        max_entry_bytes: u64,
    ) -> PyResult<()> {
        let reader = self
            .reader
            .as_mut()
            .ok_or_else(|| PyValueError::new_err("archive is closed"))?;
        py.detach(|| reader.extract_to(index, &destination, max_entry_bytes))
            .map_err(python_error)
    }

    fn close(&mut self) {
        self.reader = None;
    }
}
