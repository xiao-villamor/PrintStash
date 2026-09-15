//! Bounded native execution and process-wide admission. No database ownership.
use pyo3::{
    exceptions::{PyRuntimeError, PyTimeoutError, PyValueError},
    prelude::*,
    types::PyTuple,
};
use std::{
    panic::{catch_unwind, AssertUnwindSafe},
    sync::{Arc, Condvar, Mutex, MutexGuard},
    time::Duration,
};

fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|e| e.into_inner())
}

#[derive(Default)]
struct Admission {
    used: u64,
    jobs: usize,
}
#[derive(Default)]
struct Gate {
    state: Mutex<Admission>,
    changed: Condvar,
}
#[pyclass(module = "printstash_mesh_native")]
#[derive(Default)]
pub struct NativeBudget {
    gate: Arc<Gate>,
}
#[pyclass(module = "printstash_mesh_native")]
pub struct NativeReservation {
    gate: Arc<Gate>,
    size: u64,
    released: bool,
}
impl Drop for NativeReservation {
    fn drop(&mut self) {
        self.release();
    }
}
#[pymethods]
impl NativeReservation {
    fn release(&mut self) {
        if !self.released {
            let mut state = lock(&self.gate.state);
            state.used -= self.size;
            state.jobs -= 1;
            self.released = true;
            self.gate.changed.notify_all();
        }
    }
}
#[pymethods]
impl NativeBudget {
    #[new]
    fn new() -> Self {
        Self::default()
    }
    #[getter]
    fn used(&self) -> u64 {
        lock(&self.gate.state).used
    }
    #[getter]
    fn jobs(&self) -> usize {
        lock(&self.gate.state).jobs
    }
    #[pyo3(signature=(size, capacity, jobs, wait=true))]
    fn acquire(
        &self,
        py: Python<'_>,
        size: u64,
        capacity: u64,
        jobs: usize,
        wait: bool,
    ) -> PyResult<Option<NativeReservation>> {
        if capacity == 0 || jobs == 0 {
            return Err(PyValueError::new_err("invalid admission capacity"));
        }
        let size = size.max(1).min(capacity);
        let accepted = py.detach(|| {
            let mut state = lock(&self.gate.state);
            while state.used > capacity - size || state.jobs >= jobs {
                if !wait {
                    return false;
                }
                state = self
                    .gate
                    .changed
                    .wait(state)
                    .unwrap_or_else(|e| e.into_inner());
            }
            state.used += size;
            state.jobs += 1;
            true
        });
        Ok(accepted.then(|| NativeReservation {
            gate: self.gate.clone(),
            size,
            released: false,
        }))
    }
}

type Outcome = Result<Py<PyAny>, String>;
#[derive(Default)]
struct Completion {
    result: Mutex<Option<Outcome>>,
    changed: Condvar,
}
#[pyclass(module = "printstash_mesh_native")]
pub struct NativeTask {
    completion: Arc<Completion>,
}
#[pymethods]
impl NativeTask {
    #[pyo3(signature=(timeout=None))]
    fn result(&self, py: Python<'_>, timeout: Option<f64>) -> PyResult<Py<PyAny>> {
        if timeout.is_some_and(|v| !v.is_finite() || !(0.0..=86400.0).contains(&v)) {
            return Err(PyValueError::new_err("invalid wait timeout"));
        }
        py.detach(|| {
            let result = lock(&self.completion.result);
            if let Some(seconds) = timeout {
                let (result, _) = self
                    .completion
                    .changed
                    .wait_timeout_while(result, Duration::from_secs_f64(seconds), |r| r.is_none())
                    .unwrap_or_else(|e| e.into_inner());
                if result.is_none() {
                    return Err(PyTimeoutError::new_err("mesh task is pending"));
                }
            } else {
                drop(
                    self.completion
                        .changed
                        .wait_while(result, |r| r.is_none())
                        .unwrap_or_else(|e| e.into_inner()),
                );
            }
            Ok(())
        })?;
        match lock(&self.completion.result)
            .as_ref()
            .expect("completed task")
        {
            Ok(value) => Ok(value.clone_ref(py)),
            Err(error) => Err(PyRuntimeError::new_err(error.clone())),
        }
    }
}
#[derive(Default)]
struct PoolState {
    pending: usize,
    closed: bool,
    cancel: bool,
}
#[derive(Default)]
struct PoolControl {
    state: Mutex<PoolState>,
    changed: Condvar,
}
#[pyclass(module = "printstash_mesh_native")]
pub struct NativeExecutor {
    pool: rayon::ThreadPool,
    control: Arc<PoolControl>,
    limit: usize,
}
#[pymethods]
impl NativeExecutor {
    #[new]
    fn new(max_workers: usize) -> PyResult<Self> {
        if !(1..=32).contains(&max_workers) {
            return Err(PyValueError::new_err("invalid worker count"));
        }
        let pool = rayon::ThreadPoolBuilder::new()
            .num_threads(max_workers)
            .thread_name(|i| format!("mesh-native-{i}"))
            .build()
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        Ok(Self {
            pool,
            control: Arc::default(),
            limit: max_workers * 2,
        })
    }
    #[pyo3(signature=(function,*args))]
    fn submit(&self, function: Py<PyAny>, args: Bound<'_, PyTuple>) -> PyResult<NativeTask> {
        if !function.bind(args.py()).is_callable() {
            return Err(PyValueError::new_err("task must be callable"));
        }
        let mut state = lock(&self.control.state);
        if state.closed {
            return Err(PyRuntimeError::new_err(
                "cannot schedule new futures after shutdown",
            ));
        }
        if state.pending >= self.limit {
            return Err(PyRuntimeError::new_err("native task queue is full"));
        }
        state.pending += 1;
        let args = args.unbind();
        let control = self.control.clone();
        let completion = Arc::new(Completion::default());
        let output = completion.clone();
        self.pool.spawn_fifo(move || {
            let result = catch_unwind(AssertUnwindSafe(|| {
                if lock(&control.state).cancel {
                    return Err("mesh task cancelled".to_owned());
                }
                Python::attach(|py| function.call1(py, args).map_err(|e| e.to_string()))
            }))
            .unwrap_or_else(|_| Err("native mesh task panicked".to_owned()));
            *lock(&output.result) = Some(result);
            output.changed.notify_all();
            let mut state = lock(&control.state);
            state.pending -= 1;
            control.changed.notify_all();
        });
        Ok(NativeTask { completion })
    }
    #[pyo3(signature=(wait=true, *, cancel_futures=false))]
    fn shutdown(&self, py: Python<'_>, wait: bool, cancel_futures: bool) -> PyResult<()> {
        if wait && self.pool.current_thread_index().is_some() {
            return Err(PyRuntimeError::new_err(
                "a worker cannot wait for its own pool",
            ));
        }
        py.detach(|| {
            let mut state = lock(&self.control.state);
            state.closed = true;
            state.cancel |= cancel_futures;
            if wait {
                drop(
                    self.control
                        .changed
                        .wait_while(state, |s| s.pending > 0)
                        .unwrap_or_else(|e| e.into_inner()),
                );
            }
        });
        Ok(())
    }
}
