//! Type translation for the framework-neutral G-code core.

use std::path::PathBuf;

use printstash_gcode_core::{MaterialRequirement, Metadata};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};

fn requirement_dict<'py>(
    py: Python<'py>,
    requirement: MaterialRequirement,
) -> PyResult<Bound<'py, PyDict>> {
    let output = PyDict::new(py);
    output.set_item("tool_index", requirement.tool_index)?;
    output.set_item("material_type", requirement.material_type)?;
    output.set_item("color_hex", requirement.color_hex)?;
    Ok(output)
}

fn metadata_dict<'py>(py: Python<'py>, metadata: Metadata) -> PyResult<Bound<'py, PyDict>> {
    let output = PyDict::new(py);
    output.set_item("slicer_name", metadata.slicer_name)?;
    output.set_item("slicer_version", metadata.slicer_version)?;
    output.set_item("printer_model", metadata.printer_model)?;
    output.set_item("nozzle_diameter_mm", metadata.nozzle_diameter_mm)?;
    output.set_item("layer_height_mm", metadata.layer_height_mm)?;
    output.set_item("first_layer_height_mm", metadata.first_layer_height_mm)?;
    output.set_item("infill_percent", metadata.infill_percent)?;
    output.set_item("wall_loops", metadata.wall_loops)?;
    output.set_item("top_shell_layers", metadata.top_shell_layers)?;
    output.set_item("bottom_shell_layers", metadata.bottom_shell_layers)?;
    output.set_item("support_material", metadata.support_material)?;
    output.set_item("nozzle_temperature_c", metadata.nozzle_temperature_c)?;
    output.set_item("bed_temperature_c", metadata.bed_temperature_c)?;
    output.set_item("estimated_time_s", metadata.estimated_time_s)?;
    output.set_item("filament_weight_g", metadata.filament_weight_g)?;
    output.set_item("filament_length_mm", metadata.filament_length_mm)?;
    output.set_item("filament_cost", metadata.filament_cost)?;
    output.set_item("material_type", metadata.material_type)?;
    output.set_item("material_brand", metadata.material_brand)?;
    output.set_item("printer_preset_name", metadata.printer_preset_name)?;
    if let Some(requirements) = metadata.material_requirements {
        let values = PyList::empty(py);
        for requirement in requirements {
            values.append(requirement_dict(py, requirement)?)?;
        }
        output.set_item("material_requirements", values)?;
    } else {
        output.set_item("material_requirements", py.None())?;
    }
    Ok(output)
}

#[pyfunction]
pub fn parse_gcode_metadata<'py>(py: Python<'py>, path: PathBuf) -> PyResult<Bound<'py, PyDict>> {
    metadata_dict(py, py.detach(|| printstash_gcode_core::parse(&path)))
}

#[pyfunction]
pub fn parse_gcode_duration(value: &str) -> Option<u64> {
    printstash_gcode_core::parse_duration(value)
}

#[pyfunction]
pub fn is_bgcode(py: Python<'_>, path: PathBuf) -> bool {
    py.detach(|| printstash_gcode_core::is_bgcode(&path))
}

#[pyfunction]
pub fn is_valid_bgcode(py: Python<'_>, path: PathBuf) -> bool {
    py.detach(|| printstash_gcode_core::is_valid_bgcode(&path))
}

#[pyfunction]
pub fn bgcode_metadata_text(py: Python<'_>, path: PathBuf) -> Option<String> {
    py.detach(|| printstash_gcode_core::bgcode_metadata_text(&path))
}

#[pyfunction]
pub fn gcode_thumbnails<'py>(py: Python<'py>, path: PathBuf) -> PyResult<Bound<'py, PyList>> {
    let thumbnails = py.detach(|| printstash_gcode_core::thumbnails(&path));
    let output = PyList::empty(py);
    for thumbnail in thumbnails {
        output.append((
            thumbnail.format.code(),
            thumbnail.width,
            thumbnail.height,
            PyBytes::new(py, &thumbnail.data),
        ))?;
    }
    Ok(output)
}
