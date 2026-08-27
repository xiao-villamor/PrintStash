import { describe, expect, it } from "vitest";

import { CURRENCY_OPTIONS, formatCurrency } from "../currency";

describe("currency", () => {
  it("formats real values and distinguishes zero from missing data", () => {
    expect(formatCurrency(null, "USD")).toBe("—");
    expect(formatCurrency(0, "USD")).toContain("0.00");
    expect(formatCurrency(12.5, "EUR")).toContain("12.50");
    expect(formatCurrency(5, "")).toContain("5.00");
    expect(formatCurrency(5, "NOTACODE")).toBe("5.00 NOTACODE");
  });

  it("exports the existing well-formed picker options", () => {
    expect(CURRENCY_OPTIONS).toHaveLength(15);
    for (const option of CURRENCY_OPTIONS) {
      expect(option.code).toMatch(/^[A-Z]{3}$/);
      expect(option.label).not.toBe("");
    }
  });
});
