//! Stream mesh coordinates into packed arrays; retain only small scene XML.
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use quick_xml::events::{BytesStart, Event};
use quick_xml::{Reader, Writer, XmlVersion};
use std::io::{self, BufReader, Read};
use std::str::FromStr;

struct Source {
    object: Py<PyAny>,
    remaining: u64,
}

impl Read for Source {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        let requested = buffer
            .len()
            .min(65_536)
            .min(self.remaining.saturating_add(1) as usize);
        Python::attach(|py| {
            let value = self
                .object
                .bind(py)
                .call_method1("read", (requested,))
                .map_err(|e| io::Error::other(e.to_string()))?;
            let bytes = value
                .cast::<PyBytes>()
                .map_err(|e| io::Error::other(e.to_string()))?;
            let bytes = bytes.as_bytes();
            if bytes.len() > requested || bytes.len() as u64 > self.remaining {
                return Err(io::Error::other("3MF XML input limit exceeded"));
            }
            buffer[..bytes.len()].copy_from_slice(bytes);
            self.remaining -= bytes.len() as u64;
            Ok(bytes.len())
        })
    }
}

#[derive(Default)]
pub(crate) struct Mesh {
    pub(crate) vertices: Vec<[f64; 3]>,
    pub(crate) faces: Vec<[u64; 3]>,
}

fn triple<T: FromStr + Copy>(
    event: &BytesStart<'_>,
    names: [&str; 3],
    kind: &str,
) -> Result<[T; 3], String> {
    let mut values = [None; 3];
    for attr in event.attributes() {
        let attr = attr.map_err(|e| e.to_string())?;
        if let Some(index) = names.iter().position(|name| *name == attr.key.as_ref()) {
            let value = attr
                .normalized_value(XmlVersion::Implicit1_0)
                .map_err(|e| e.to_string())?;
            values[index] = Some(
                value
                    .trim()
                    .parse()
                    .map_err(|_| format!("invalid {kind}"))?,
            );
        }
    }
    match values {
        [Some(a), Some(b), Some(c)] => Ok([a, b, c]),
        _ => Err(format!("missing {kind} attribute")),
    }
}

pub(crate) fn parse(source: impl Read) -> Result<(Vec<u8>, Vec<Mesh>), String> {
    let mut reader = Reader::from_reader(BufReader::with_capacity(65_536, source));
    let mut writer = Writer::new(Vec::new());
    let mut buffer = Vec::new();
    let mut stack: Vec<String> = Vec::new();
    let mut mesh_depth = None;
    let mut mesh = Mesh::default();
    let mut meshes = Vec::new();
    loop {
        let event = reader
            .read_event_into(&mut buffer)
            .map_err(|e| format!("invalid XML: {e}"))?;
        match &event {
            Event::DocType(_) => return Err("3MF XML DTD is not supported".into()),
            Event::Start(tag) | Event::Empty(tag) => {
                let local = tag.local_name();
                let name = local.as_ref();
                let empty = matches!(event, Event::Empty(_));
                if name == "mesh" {
                    if mesh_depth.is_some() {
                        return Err("nested XML mesh".into());
                    }
                    writer
                        .write_event(Event::Empty(tag.borrow()))
                        .map_err(|e| e.to_string())?;
                    if empty {
                        meshes.push(Mesh::default());
                    } else {
                        mesh_depth = Some(stack.len());
                    }
                } else if mesh_depth.is_some() {
                    if name == "vertex" && stack.last().is_some_and(|s| s == "vertices") {
                        let v: [f64; 3] = triple(tag, ["x", "y", "z"], "coordinate")?;
                        if !v.iter().all(|v| v.is_finite()) {
                            return Err("coordinates must be finite".into());
                        }
                        mesh.vertices.push(v);
                    } else if name == "triangle" && stack.last().is_some_and(|s| s == "triangles") {
                        mesh.faces
                            .push(triple(tag, ["v1", "v2", "v3"], "face index")?);
                    }
                } else {
                    writer
                        .write_event(event.borrow())
                        .map_err(|e| e.to_string())?;
                }
                if !empty {
                    stack.push(name.to_owned());
                    if stack.len() > 256 {
                        return Err("XML nesting limit exceeded".into());
                    }
                }
            }
            Event::End(_) => {
                stack.pop().ok_or("invalid XML closing tag")?;
                if mesh_depth == Some(stack.len()) {
                    if mesh
                        .faces
                        .iter()
                        .flatten()
                        .any(|&i| i >= mesh.vertices.len() as u64)
                    {
                        return Err("face index outside vertex array".into());
                    }
                    meshes.push(std::mem::take(&mut mesh));
                    mesh_depth = None;
                } else if mesh_depth.is_none() {
                    writer
                        .write_event(event.borrow())
                        .map_err(|e| e.to_string())?;
                }
            }
            Event::Eof => {
                if !stack.is_empty() {
                    return Err("incomplete XML".into());
                }
                break;
            }
            _ if mesh_depth.is_none() => writer
                .write_event(event.borrow())
                .map_err(|e| e.to_string())?,
            _ => (),
        }
        if writer.get_ref().len() > 16 * 1024 * 1024 {
            return Err("XML metadata limit exceeded".into());
        }
        buffer.clear();
    }
    Ok((writer.into_inner(), meshes))
}

type PackedMesh<'py> = (Bound<'py, PyBytes>, Bound<'py, PyBytes>);
pub(crate) type ParsedModel<'py> = (Bound<'py, PyBytes>, Vec<PackedMesh<'py>>);

#[pyfunction]
#[pyo3(signature = (source, max_bytes=536_870_912))]
pub fn parse_3mf_xml<'py>(
    py: Python<'py>,
    source: Py<PyAny>,
    max_bytes: u64,
) -> PyResult<ParsedModel<'py>> {
    let (shell, meshes) = py
        .detach(|| {
            parse(Source {
                object: source,
                remaining: max_bytes,
            })
        })
        .map_err(PyValueError::new_err)?;
    pack(py, shell, meshes)
}

pub(crate) fn pack<'py>(
    py: Python<'py>,
    shell: Vec<u8>,
    meshes: Vec<Mesh>,
) -> PyResult<ParsedModel<'py>> {
    let mut output = Vec::with_capacity(meshes.len());
    for mesh in meshes {
        let vertices = PyBytes::new_with(py, mesh.vertices.len() * 24, |bytes| {
            py.detach(|| {
                for (value, target) in mesh
                    .vertices
                    .iter()
                    .flatten()
                    .zip(bytes.as_chunks_mut::<8>().0.iter_mut())
                {
                    target.copy_from_slice(&value.to_ne_bytes());
                }
            });
            Ok(())
        })?;
        let faces = PyBytes::new_with(py, mesh.faces.len() * 24, |bytes| {
            py.detach(|| {
                for (value, target) in mesh
                    .faces
                    .iter()
                    .flatten()
                    .zip(bytes.as_chunks_mut::<8>().0.iter_mut())
                {
                    target.copy_from_slice(&value.to_ne_bytes());
                }
            });
            Ok(())
        })?;
        output.push((vertices, faces));
    }
    Ok((PyBytes::new(py, &shell), output))
}
