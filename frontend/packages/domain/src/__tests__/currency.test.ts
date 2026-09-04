/*
 * Money, for a self-hoster who may not be American.
 *
 * The currency code comes out of a settings field a user typed, so this has to
 * survive three inputs the UI cannot prevent: nothing (null cost), an empty code,
 * and a code `Intl` has never heard of. None of them may throw — a cost cell that
 * crashes takes the whole model page with it — and none may show a number that
 * misleads: `null` is an em dash rather than "$0.00", because a print with no
 * recorded cost is not a free print.
 *
 * A real zero still renders as currency. That is the same distinction the
 * formatters in `format.test.ts` make, and it is the one users notice.
 */

import { describe, expect, it } from "vitest";

import { CURRENCY_OPTIONS, formatCurrency } from "../currency";

describe("formatCurrency", () => {
  it("renders an em dash for null/undefined (not a misleading $0.00)", () => {
    expect(formatCurrency(null, "USD")).toBe("—");
    expect(formatCurrency(undefined, "USD")).toBe("—");
  });

  it("preserves a real zero amount", () => {
    // Unlike null, an actual 0 cost should render as currency, not an em dash.
    const out = formatCurrency(0, "USD");
    expect(out).not.toBe("—");
    expect(out).toContain("0.00");
  });

  it("formats the amount through Intl for any valid code", () => {
    // Locale-agnostic: assert the amount/grouping survive, not symbol placement.
    expect(formatCurrency(12.5, "USD")).toContain("12.50");
    expect(formatCurrency(12.5, "EUR")).toContain("12.50");
  });

  it("defaults an empty code to USD rather than throwing", () => {
    const out = formatCurrency(5, "");
    expect(out).not.toBe("—");
    expect(out).toContain("5.00");
  });

  it("falls back to a number + code suffix for an invalid code", () => {
    // An invalid ISO code makes Intl throw; we degrade gracefully.
    expect(formatCurrency(5, "NOTACODE")).toBe("5.00 NOTACODE");
  });

  it("ships a non-empty, well-formed picker shortlist", () => {
    expect(CURRENCY_OPTIONS.length).toBeGreaterThan(0);
    for (const option of CURRENCY_OPTIONS) {
      expect(option.code).toMatch(/^[A-Z]{3}$/);
      expect(option.label.length).toBeGreaterThan(0);
    }
  });
});
