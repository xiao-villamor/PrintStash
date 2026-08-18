import { describe, expect, it } from "vitest";

import { PRINTER_SETUP_OPTIONS, setupProviderFields } from "@/lib/printer-providers";
import { SHARED_PRINTER_CONTRACT } from "../printer-contracts";

const PROVIDER_IDS = [
  "moonraker",
  "bambu_lan",
  "prusalink",
  "elegoo_centauri",
  "octoprint",
] as const;

describe("generated printer contracts", () => {
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
    const supports = (provider: (typeof PROVIDER_IDS)[number], capability: string) =>
      SHARED_PRINTER_CONTRACT.providers[provider].capabilities.includes(
        capability as never,
      );

    expect(supports("moonraker", "send_gcode")).toBe(true);
    expect(supports("bambu_lan", "upload")).toBe(true);
    expect(supports("bambu_lan", "list_files")).toBe(false);
    expect(supports("prusalink", "delete_file")).toBe(true);
    expect(supports("octoprint", "send_gcode")).toBe(false);
    expect(supports("elegoo_centauri", "upload")).toBe(true);
    expect(supports("elegoo_centauri", "delete_file")).toBe(false);
  });
});
