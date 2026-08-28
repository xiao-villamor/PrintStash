/*
 * The generated printer catalog still matches the product it describes.
 *
 * This file is generated from the backend's provider definitions, so nothing in
 * the frontend fails when it drifts — the setup form simply stops offering a
 * provider, or offers one twice. Both are silent: the first looks like the
 * provider was never supported, the second like a rendering glitch.
 *
 * The capability row is the one with behaviour attached. Which buttons a printer
 * card shows is derived from declared capabilities, so a capability lost in
 * regeneration hides pause or cancel on a machine that supports them.
 */

import { describe, expect, it } from "vitest";

import { PRINTER_SETUP_OPTIONS, setupProviderFields } from "@/lib/printer-providers";
import {
  SHARED_PRINTER_CONTRACT,
  type SharedPrinterCapability,
  type SharedPrinterProviderId,
} from "../printer-contracts";

const PROVIDER_IDS = [
  "moonraker",
  "bambu_lan",
  "prusalink",
  "elegoo_centauri",
  "octoprint",
] as const;

describe("PRINTER_CONTRACTS", () => {
  it("keeps the generated catalog aligned with the product setup overlay", () => {
    expect(
      SHARED_PRINTER_CONTRACT.setupOptions.map(({ value, provider, variant, label }) => [
        value,
        provider,
        variant,
        label,
      ]),
    ).toEqual(
      PRINTER_SETUP_OPTIONS.map(({ value, label }) => {
        const fields = setupProviderFields(value);
        return [value, fields.provider, fields.provider_variant ?? null, label];
      }),
    );
  });

  it("contains every public provider exactly once", () => {
    expect(Object.keys(SHARED_PRINTER_CONTRACT.providers)).toEqual(PROVIDER_IDS);
  });

  it("drives action visibility from declared capabilities", () => {
    const supports = (provider: SharedPrinterProviderId, capability: SharedPrinterCapability) => {
      // Each provider declares its own narrower tuple of capabilities; read it back
      // through the full capability union so membership is a plain lookup.
      const declared: readonly SharedPrinterCapability[] =
        SHARED_PRINTER_CONTRACT.providers[provider].capabilities;
      return declared.includes(capability);
    };

    expect(supports("moonraker", "send_gcode")).toBe(true);
    expect(supports("bambu_lan", "upload")).toBe(true);
    expect(supports("bambu_lan", "list_files")).toBe(false);
    expect(supports("prusalink", "delete_file")).toBe(true);
    expect(supports("octoprint", "send_gcode")).toBe(false);
    expect(supports("elegoo_centauri", "upload")).toBe(true);
    expect(supports("elegoo_centauri", "delete_file")).toBe(false);
  });
});
