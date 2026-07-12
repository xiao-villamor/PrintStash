import { describe, expect, it } from "vitest";

import { providerAddress, providerLabel, setupProviderFields } from "../printer-providers";

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
