//! PyO3 translation boundary for the framework-free acquisition core.
use std::path::PathBuf;

use printstash_acquisition_core::{self as core, DownloadRequest, Target};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyAny;

fn error(error: core::Error) -> PyErr {
    PyValueError::new_err(error.code())
}

/// Python-owned handle to a staged native download.
///
/// The core retains the temporary file until Python selects the final suffix and
/// requests create-only publication. Dropping an unpublished handle removes it.
#[pyclass]
pub struct NativeDownload {
    inner: core::Download,
}

#[pymethods]
impl NativeDownload {
    #[getter]
    fn status(&self) -> u16 {
        self.inner.status()
    }

    #[getter]
    fn location(&self) -> Option<&str> {
        self.inner.location()
    }

    #[getter]
    fn content_disposition(&self) -> Option<&str> {
        self.inner.content_disposition()
    }

    #[getter]
    fn written(&self) -> u64 {
        self.inner.written()
    }

    #[getter]
    fn digest(&self) -> Option<&str> {
        self.inner.digest()
    }

    fn publish(&mut self, destination: PathBuf) -> PyResult<String> {
        self.inner.publish(destination).map_err(error)
    }
}

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn download_to_staging<'py>(
    py: Python<'py>,
    url: String,
    host: String,
    ip: String,
    port: u16,
    directory: PathBuf,
    byte_limit: u64,
    timeout_seconds: f64,
) -> PyResult<Bound<'py, PyAny>> {
    pyo3_async_runtimes::tokio::future_into_py(py, async move {
        let target = Target::try_new(url, host, ip, port).map_err(error)?;
        let request = DownloadRequest::try_new(target, directory, byte_limit, timeout_seconds)
            .map_err(error)?;
        let inner = core::download(request).await.map_err(error)?;
        Ok(NativeDownload { inner })
    })
}
