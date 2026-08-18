import { describe, expect, it } from "vitest";

import {
  PRINTER_SETUP_OPTIONS,
  providerLabel,
  setupProviderFields,
} from "../printer-providers";
import type { PrinterProvider } from "@/types";
import type { PrinterVariant } from "@/types/printers";

const EXPECTED_SETUP_CATALOG = [
  ["moonraker", "Moonraker / Klipper", "moonraker", null, "Moonraker"],
  [
    "elegoo_neptune4",
    "Elegoo Neptune 4 family",
    "moonraker",
    "elegoo_neptune4",
    "Elegoo Neptune 4 / Moonraker",
  ],
  ["prusalink", "PrusaLink (beta)", "prusalink", null, "PrusaLink"],
  ["octoprint", "OctoPrint (beta)", "octoprint", null, "OctoPrint"],
  [
    "elegoo_centauri_carbon",
    "Elegoo Centauri Carbon (beta)",
    "elegoo_centauri",
    "elegoo_centauri_carbon",
    "Elegoo Centauri Carbon",
  ],
  [
    "elegoo_centauri_carbon_2",
    "Elegoo Centauri Carbon 2 (beta)",
    "elegoo_centauri",
    "elegoo_centauri_carbon_2",
    "Elegoo Centauri Carbon 2",
  ],
  ["bambu_lan", "Bambu LAN (beta)", "bambu_lan", null, "Bambu LAN"],
] as const;

describe("printer provider public contract", () => {
  it("keeps setup kinds, labels, and transport mappings stable", () => {
    expect(
      PRINTER_SETUP_OPTIONS.map(({ value, label }) => {
        const fields = setupProviderFields(value);
        return [value, label, fields.provider, fields.provider_variant ?? null];
      }),
    ).toEqual(EXPECTED_SETUP_CATALOG.map((entry) => entry.slice(0, 4)));
  });

  it("keeps every direct provider represented by a setup option", () => {
    const providers = [
      "moonraker",
      "bambu_lan",
      "prusalink",
      "elegoo_centauri",
      "octoprint",
    ] satisfies PrinterProvider[];

    const mappedProviders = new Set(
      PRINTER_SETUP_OPTIONS.map(({ value }) => setupProviderFields(value).provider),
    );
    expect(mappedProviders).toEqual(new Set(providers));
  });

  it.each(EXPECTED_SETUP_CATALOG)(
    "keeps the display label for %s stable",
    (kind, _setupLabel, provider, variant, displayLabel) => {
      expect(
        providerLabel({
          provider: provider as PrinterProvider,
          provider_variant: variant as PrinterVariant | null,
        }),
      ).toBe(displayLabel);
    },
  );
});
