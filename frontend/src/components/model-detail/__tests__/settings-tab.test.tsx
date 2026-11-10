/*
 * Telling a user why the slicer-settings tab is empty.
 *
 * An STL has no slicer settings — there is nothing to show and nothing wrong. An
 * empty tab with no explanation reads as data that failed to load, so the user
 * re-uploads the file, or reports a bug. The sentence is the feature.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SettingsTab } from "@/components/model-detail/settings-tab";
import { DEFAULT_METADATA_PREFERENCES } from "@/lib/metadata-preferences";
import { buildPrintSettingRows } from "@/components/model-detail/presentation";
import { aMetadata } from "@/test-support/factories";
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
  layer_count: null,
  exposure_time_s: null,
  resin_volume_ml: null,
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
  volume_measurement: {
    state: "legacy_unassessed",
    unit: "mm3",
    method: null,
    value_mm3: 176138.91,
    cause: null,
  },
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

function setting(label: string) {
  const row = screen.getByText(label).parentElement;
  if (!row) throw new Error("A setting must belong to its definition row");
  return within(row);
}

function renderSettings(meta: MetadataRead, preferences = DEFAULT_METADATA_PREFERENCES) {
  return render(
    <SettingsTab
      meta={meta}
      printSettingRows={buildPrintSettingRows(meta, preferences)}
      preferences={preferences}
    />,
  );
}

describe("SettingsTab recorded values", () => {
  it("groups settings into the reference sections", () => {
    renderSettings(aMetadata({ printer_model: "Workshop printer" }));

    expect(screen.getByRole("heading", { name: "Printer & material" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Temperatures" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Estimates" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Layers & structure" })).toBeVisible();
  });
  it("preserves recorded zero settings", () => {
    renderSettings(
      aMetadata({
        layer_height_mm: 0,
        estimated_time_s: 0,
        filament_weight_g: 0,
        filament_cost: 0,
      }),
    );

    expect(setting("Layer height").getByText("0mm")).toBeVisible();
    expect(setting("Estimated time").getByText("0s")).toBeVisible();
    expect(setting("Filament weight").getByText("0g")).toBeVisible();
    expect(setting("Filament cost").getByText("0.00")).toBeVisible();
  });
  it("retains recorded zero walls", () => {
    renderSettings(aMetadata({ wall_loops: 0 }));

    expect(setting("Walls").getByText("0")).toBeVisible();
  });
  it("distinguishes absent walls from recorded zero", () => {
    renderSettings(aMetadata());

    expect(setting("Walls").getByText("Not recorded")).toBeVisible();
  });
  it("retains a recorded false support setting", () => {
    renderSettings(aMetadata({ support_material: false }));

    expect(setting("Supports").getByText("No")).toBeVisible();
  });
  it("retains zero resin exposure", () => {
    renderSettings(aMetadata({ layer_count: 0, exposure_time_s: 0, resin_volume_ml: 0 }));

    expect(setting("EXPOSURE").getByText("0 s")).toBeVisible();
    expect(setting("RESIN VOLUME").getByText("0 ml")).toBeVisible();
  });
  it("honors a hidden printer profile preference", () => {
    renderSettings(aMetadata({ printer_model: "Workshop printer" }), {
      ...DEFAULT_METADATA_PREFERENCES,
      printer_profile: false,
    });

    expect(screen.queryByText("Workshop printer")).not.toBeInTheDocument();
  });
  it("retains zero mesh counts", () => {
    renderSettings(aMetadata({ volume_mm3: 0, triangle_count: 0 }));

    expect(setting("TRIANGLES").getByText("0")).toBeVisible();
    expect(setting("VOLUME").getByText("0.0 mm³")).toBeVisible();
  });
});
