import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SettingsTab } from "@/components/model-detail/settings-tab";
import { DEFAULT_METADATA_PREFERENCES } from "@/lib/metadata-preferences";
import { buildPrintSettingRows } from "@/components/model-detail/presentation";
import type { MetadataRead } from "@/types";

const geometryOnlyMetadata: MetadataRead = {
  slicer_name: null,
  slicer_version: null,
  printer_model: null,
  nozzle_diameter_mm: null,
  layer_height_mm: null,
  first_layer_height_mm: null,
  infill_percent: null,
  wall_loops: null,
  top_shell_layers: null,
  bottom_shell_layers: null,
  support_material: null,
  nozzle_temperature_c: null,
  bed_temperature_c: null,
  estimated_time_s: null,
  filament_weight_g: null,
  filament_length_mm: null,
  filament_cost: null,
  material_type: null,
  material_brand: null,
  bbox_x_mm: null,
  bbox_y_mm: null,
  bbox_z_mm: null,
  volume_mm3: 176138.91,
  triangle_count: 49672,
};

describe("SettingsTab", () => {
  it("explains why a geometry-only file has no slicer settings", () => {
    render(
      <SettingsTab
        meta={geometryOnlyMetadata}
        printSettingRows={buildPrintSettingRows(geometryOnlyMetadata, DEFAULT_METADATA_PREFERENCES)}
        preferences={DEFAULT_METADATA_PREFERENCES}
      />,
    );

    expect(
      screen.getByText(
        "This file contains mesh geometry only. Printer, material, and slicer settings are not embedded. Add a sliced G-code or 3MF revision to capture them.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("Mesh Geometry")).toBeInTheDocument();
    expect(screen.getByText("176.14 cm³")).toBeInTheDocument();
    expect(screen.getByText("49,672")).toBeInTheDocument();
  });
});
