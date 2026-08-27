import { describe, expect, it } from "vitest";

import {
  PRINTER_SETUP_OPTIONS,
  providerAddress,
  providerLabel,
  setupProviderFields,
  type PrinterSetupKind,
} from "../printer-providers";
import type { PrinterProvider } from "@/types";
import type { PrinterVariant } from "@/types/printers";

type SetupCatalogRow = readonly [
  kind: PrinterSetupKind,
  setupLabel: string,
  provider: PrinterProvider,
  variant: PrinterVariant | null,
  displayLabel: string,
];

const EXPECTED_SETUP_CATALOG: readonly SetupCatalogRow[] = [
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
];

describe("printer provider metadata", () => {
  it("maps Elegoo preset onto Moonraker transport", () => {
    expect(setupProviderFields("elegoo_neptune4")).toEqual({
      provider: "moonraker",
      provider_variant: "elegoo_neptune4",
    });
  });

  it("labels and addresses PrusaLink", () => {
    const printer = {
      provider: "prusalink" as const,
      provider_variant: null,
      moonraker_url: "",
      bambu_host: null,
      prusalink_url: "http://mk4.local",
    };
    expect(providerLabel(printer)).toBe("PrusaLink");
    expect(providerAddress(printer)).toBe("http://mk4.local");
  });

  it("maps both Centauri models onto dedicated provider variants", () => {
    expect(setupProviderFields("elegoo_centauri_carbon")).toEqual({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon",
    });
    expect(setupProviderFields("elegoo_centauri_carbon_2")).toEqual({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon_2",
    });
  });
});

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
      expect(providerLabel({ provider, provider_variant: variant })).toBe(displayLabel);
    },
  );
});
