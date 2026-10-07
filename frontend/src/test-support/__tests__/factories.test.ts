/**
 * The builders are load-bearing, so their promises are tested like anything else.
 *
 * Every frontend test that needs an API-shaped object gets it from here, which
 * makes a wrong builder worse than a wrong test: it is wrong everywhere at once,
 * and quietly. The promises worth pinning are that a builder returns the
 * *ordinary* state (so an interesting variant is visible at the call site), that
 * an override replaces only what it names, and that two builds never share a
 * nested object — a shared one lets one test's mutation leak into the next.
 */

import { describe, expect, it } from "vitest";

import {
  FROZEN_NOW,
  aMetadata,
  aModelListItem,
  aOutlinerModel,
  aPrinter,
  aPrintJob,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";

import type { VolumeMeasurement } from "@/types/models";

describe("aPrinter", () => {
  it("returns a reachable printer the caller may operate", () => {
    const printer = aPrinter();

    // The ordinary case. A test asserting a control is hidden has to turn
    // something off, which is exactly the signal a reader wants.
    expect(printer.status).toBe("ready");
    expect(printer.access.can_print).toBe(true);
    expect(printer.capabilities.can_start).toBe(true);
  });

  it("applies a top-level override", () => {
    expect(aPrinter({ status: "printing" }).status).toBe("printing");
  });

  it("takes a composed access block without dropping its siblings", () => {
    const printer = aPrinter({ access: printerAccess({ can_print: false }) });

    // Composition rather than a deep merge: naming one permission must not
    // blank the other four, or every RBAC test would silently lose its context.
    expect(printer.access.can_print).toBe(false);
    expect(printer.access.can_view).toBe(true);
    expect(printer.access.can_admin).toBe(true);
  });

  it("takes a composed capabilities block", () => {
    const printer = aPrinter({
      capabilities: printerCapabilities({ unsupported_actions: ["pause"] }),
    });

    expect(printer.capabilities.unsupported_actions).toEqual(["pause"]);
    expect(printer.capabilities.can_start).toBe(true);
  });

  it("does not share nested objects between two builds", () => {
    const first = aPrinter();
    const second = aPrinter();

    first.capabilities.can_start = false;

    // A shared nested object would let one test's mutation leak into the next,
    // which is an order-dependent failure a long way from its cause.
    expect(second.capabilities.can_start).toBe(true);
  });

  it("uses a fixed instant for every timestamp", () => {
    const printer = aPrinter();

    // Never `Date.now()`: a clock-derived fixture makes a relative-time
    // assertion pass only on the day it was written.
    expect(printer.created_at).toBe(FROZEN_NOW);
    expect(printer.updated_at).toBe(FROZEN_NOW);
  });
});

describe("printerAccess", () => {
  it("grants everything by default", () => {
    expect(printerAccess().can_admin).toBe(true);
  });

  it("narrows to the role a permission test needs", () => {
    const access = printerAccess({ role: "view", can_print: false, can_admin: false });

    expect(access).toMatchObject({ role: "view", can_print: false, can_view: true });
  });
});

describe("printerCapabilities", () => {
  it("enables every capability at stable support", () => {
    const capabilities = printerCapabilities();

    expect(capabilities.support_level).toBe("stable");
    expect(capabilities.can_send_gcode).toBe(true);
  });

  it("turns off only the capability named", () => {
    const capabilities = printerCapabilities({ can_pause: false });

    expect(capabilities.can_pause).toBe(false);
    expect(capabilities.can_resume).toBe(true);
  });
});

describe("aPrintJob", () => {
  it("returns a queued, vault-backed job", () => {
    const job = aPrintJob();

    // `vault` means PrintStash owns the bytes, which is the ordinary case; the
    // other evidence values describe a job seen on the printer that we could
    // not capture, and a test asks for those by name.
    expect(job.state).toBe("queued");
    expect(job.artifact_evidence).toBe("vault");
  });

  it("applies an override", () => {
    expect(aPrintJob({ state: "printing", progress: 0.5 }).progress).toBe(0.5);
  });
});

describe("aModelListItem", () => {
  it("returns a row with nothing printed yet", () => {
    const item = aModelListItem();

    expect(item.print_summary).toBeNull();
    expect(item.starred).toBe(false);
  });

  it("applies an override", () => {
    expect(aModelListItem({ name: "Gearbox" }).name).toBe("Gearbox");
  });
});

describe("aMetadata", () => {
  it("returns pending volume evidence", () => {
    const metadata = aMetadata();

    expect(metadata.volume_mm3).toBeNull();
    expect(metadata.volume_measurement).toEqual({
      state: "not_calculated",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause: "enrichment_pending",
    });
  });

  it("derives the scalar from measured evidence", () => {
    const metadata = aMetadata({
      volume_measurement: {
        state: "measured",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: 100,
        cause: null,
      },
    });

    expect(metadata.volume_mm3).toBe(100);
    expect(metadata.volume_measurement).toEqual({
      state: "measured",
      unit: "mm3",
      method: "mesh_surface_integral",
      value_mm3: 100,
      cause: null,
    });
  });

  it("accepts an exactly matching measured scalar", () => {
    const metadata = aMetadata({
      volume_mm3: 100,
      volume_measurement: {
        state: "measured",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: 100,
        cause: null,
      },
    });

    expect(metadata.volume_mm3).toBe(metadata.volume_measurement.value_mm3);
    expect(metadata.volume_measurement.state).toBe("measured");
  });

  it.each([
    "not_watertight",
    "inconsistent_winding",
    "non_positive_integral",
    "nonfinite_integral",
    "measurement_failed",
  ] as const)("preserves unavailable volume causes: %s", (cause) => {
    const metadata = aMetadata({
      volume_measurement: {
        state: "unavailable",
        unit: "mm3",
        method: "mesh_surface_integral",
        value_mm3: null,
        cause,
      },
    });

    expect(metadata.volume_mm3).toBeNull();
    expect(metadata.volume_measurement).toEqual({
      state: "unavailable",
      unit: "mm3",
      method: "mesh_surface_integral",
      value_mm3: null,
      cause,
    });
  });

  it.each([
    "enrichment_pending",
    "not_applicable",
    "not_requested",
    "geometry_unavailable",
    "topology_not_evaluated",
  ] as const)("preserves not calculated volume causes: %s", (cause) => {
    const metadata = aMetadata({
      volume_measurement: {
        state: "not_calculated",
        unit: "mm3",
        method: null,
        value_mm3: null,
        cause,
      },
    });

    expect(metadata.volume_mm3).toBeNull();
    expect(metadata.volume_measurement).toEqual({
      state: "not_calculated",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause,
    });
  });

  it.each([
    { label: "null", value: null },
    { label: "zero", value: 0 },
    { label: "positive", value: 100 },
    { label: "negative", value: -100 },
  ])("preserves historical scalar as unassessed: $label", ({ value }) => {
    const metadata = aMetadata({ volume_mm3: value });

    expect(metadata.volume_mm3).toBe(value);
    expect(metadata.volume_measurement).toEqual({
      state: "legacy_unassessed",
      unit: "mm3",
      method: null,
      value_mm3: value,
      cause: null,
    });
  });

  it("preserves explicit legacy evidence", () => {
    const metadata = aMetadata({
      volume_measurement: {
        state: "legacy_unassessed",
        unit: "mm3",
        method: null,
        value_mm3: 0,
        cause: null,
      },
    });

    expect(metadata.volume_mm3).toBe(0);
    expect(metadata.volume_measurement).toEqual({
      state: "legacy_unassessed",
      unit: "mm3",
      method: null,
      value_mm3: 0,
      cause: null,
    });
  });

  it("does not share default volume evidence", () => {
    const first = aMetadata();
    const second = aMetadata();

    Object.assign(first.volume_measurement, { cause: "not_requested" });
    expect(first.volume_measurement).not.toBe(second.volume_measurement);
    expect(second.volume_measurement).toEqual({
      state: "not_calculated",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause: "enrichment_pending",
    });
  });

  it("copies caller volume evidence", () => {
    const evidence: VolumeMeasurement = {
      state: "measured",
      unit: "mm3",
      method: "mesh_surface_integral",
      value_mm3: 100,
      cause: null,
    };
    const metadata = aMetadata({ volume_measurement: evidence });

    evidence.value_mm3 = 200;

    expect(metadata.volume_measurement.value_mm3).toBe(100);
    expect(metadata.volume_mm3).toBe(100);
  });

  it("preserves unrelated metadata overrides", () => {
    const metadata = aMetadata({ triangle_count: 12, material_type: "PLA" });

    expect(metadata.triangle_count).toBe(12);
    expect(metadata.material_type).toBe("PLA");
  });

  it.each([
    {
      state: "measured",
      unit: "mm3",
      method: "mesh_surface_integral",
      value_mm3: 100,
      cause: null,
    },
    {
      state: "unavailable",
      unit: "mm3",
      method: "mesh_surface_integral",
      value_mm3: null,
      cause: "not_watertight",
    },
    {
      state: "not_calculated",
      unit: "mm3",
      method: null,
      value_mm3: null,
      cause: "topology_not_evaluated",
    },
    { state: "legacy_unassessed", unit: "mm3", method: null, value_mm3: 0, cause: null },
  ] satisfies VolumeMeasurement[])("rejects conflicting volume scalars: $state", (evidence) => {
    expect(() => aMetadata({ volume_mm3: 200, volume_measurement: evidence })).toThrow(
      "Volume evidence must match volume_mm3",
    );
  });

  it.each([
    { label: "zero", value: 0 },
    { label: "negative", value: -100 },
    { label: "NaN", value: Number.NaN },
    { label: "infinity", value: Number.POSITIVE_INFINITY },
    { label: "negative infinity", value: Number.NEGATIVE_INFINITY },
  ])("rejects invalid measured volumes: $label", ({ value }) => {
    expect(() =>
      aMetadata({
        volume_measurement: {
          state: "measured",
          unit: "mm3",
          method: "mesh_surface_integral",
          value_mm3: value,
          cause: null,
        },
      }),
    ).toThrow("Measured volume must be finite and positive");
  });

  it.each([
    { label: "NaN", value: Number.NaN },
    { label: "infinity", value: Number.POSITIVE_INFINITY },
    { label: "negative infinity", value: Number.NEGATIVE_INFINITY },
  ])("rejects nonfinite historical scalars: $label", ({ value }) => {
    expect(() => aMetadata({ volume_mm3: value })).toThrow("Legacy volume must be finite");
  });

  it.each([
    { label: "NaN", value: Number.NaN },
    { label: "infinity", value: Number.POSITIVE_INFINITY },
    { label: "negative infinity", value: Number.NEGATIVE_INFINITY },
  ])("rejects nonfinite explicit legacy evidence: $label", ({ value }) => {
    expect(() =>
      aMetadata({
        volume_measurement: {
          state: "legacy_unassessed",
          unit: "mm3",
          method: null,
          value_mm3: value,
          cause: null,
        },
      }),
    ).toThrow("Legacy volume must be finite");
  });
});

describe("aOutlinerModel", () => {
  it.each([1, 7])("builds a minimal outliner fixture at version %s", (edit_version) => {
    const leaf = aOutlinerModel({ edit_version });
    expect(leaf.edit_version).toBe(edit_version);
    expect(leaf.collection).toBe("parts");
    expect(leaf.collection_id).toBe(1);
    expect(Object.keys(leaf).sort()).toEqual([
      "collection",
      "collection_id",
      "collection_label",
      "edit_version",
      "id",
      "name",
    ]);
  });
});
