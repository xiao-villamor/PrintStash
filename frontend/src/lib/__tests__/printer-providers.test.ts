/*
 * The catalog that turns "which printer do you have?" into a transport.
 *
 * A setup *kind* is what the user picks in the add-printer form; a *provider* is
 * the protocol that actually talks to the machine, and the two are not one to one:
 * an Elegoo Neptune is Moonraker underneath, and the two Centauri models share a
 * provider but not a variant. Those are exactly the mappings a reader gets wrong
 * from the names alone, and getting one wrong builds a client for the wrong
 * protocol — which surfaces as a printer that never connects, not as a
 * configuration error anyone can see.
 *
 * So the catalog is pinned rather than sampled. Renaming a kind or remapping a
 * transport changes what an existing form submission means, and the coverage row
 * catches the other direction: a supported provider with no setup option is a
 * printer nobody can add, with nothing anywhere saying so.
 */

import { describe, expect, it } from "vitest";

import type { PrinterProvider } from "@/types";
import type { PrinterVariant } from "@/types/printers";

import {
  PRINTER_SETUP_OPTIONS,
  providerAddress,
  providerLabel,
  setupProviderFields,
  type PrinterSetupKind,
} from "../printer-providers";

/**
 * One frozen row of the public setup catalog: the setup kind a user picks, the label
 * shown in the picker, the transport it maps to, and the label a printer on that
 * transport displays.
 */
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

describe("setupProviderFields", () => {
  it("maps the Elegoo Neptune preset onto the Moonraker transport", () => {
    expect(setupProviderFields("elegoo_neptune4")).toEqual({
      provider: "moonraker",
      provider_variant: "elegoo_neptune4",
    });
  });

  it("maps the first Centauri onto its own provider variant", () => {
    expect(setupProviderFields("elegoo_centauri_carbon")).toEqual({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon",
    });
  });

  it("maps the second Centauri onto a variant of its own", () => {
    // The two Centauri models share a provider, so a single variant for both would
    // build the right client and then talk to it in the wrong dialect.
    expect(setupProviderFields("elegoo_centauri_carbon_2")).toEqual({
      provider: "elegoo_centauri",
      provider_variant: "elegoo_centauri_carbon_2",
    });
  });
});

describe("providerLabel", () => {
  it("labels a printer by its transport when it has no variant", () => {
    expect(providerLabel({ provider: "prusalink", provider_variant: null })).toBe("PrusaLink");
  });

  it.each(EXPECTED_SETUP_CATALOG)(
    "keeps the display label for %s stable",
    (_kind, _setupLabel, provider, variant, displayLabel) => {
      expect(providerLabel({ provider, provider_variant: variant })).toBe(displayLabel);
    },
  );
});

describe("providerAddress", () => {
  it("reads the address out of the field its transport uses", () => {
    expect(
      providerAddress({
        provider: "prusalink",
        moonraker_url: "",
        bambu_host: null,
        prusalink_url: "http://mk4.local",
        elegoo_centauri_host: null,
        octoprint_url: null,
      }),
    ).toBe("http://mk4.local");
  });
});

describe("PRINTER_SETUP_OPTIONS", () => {
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
});
