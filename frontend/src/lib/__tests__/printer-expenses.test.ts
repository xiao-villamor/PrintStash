/**
 * Published print measurements supply defaults only for configured costs.
 * A custom value, including zero, takes precedence over the catalog.
 */
import { describe, expect, it } from "vitest";
import {
  averagePrintPower,
  expenseDraft,
  expenseProblem,
  parseExpenses,
} from "@/lib/printer-expenses";

describe("printer expense defaults", () => {
  it.each([
    ["  BAMBU LAB A1 ", 95],
    ["Bambu Lab A1 mini", 57],
    ["Prusa MK3S+", 80],
    ["Prusa MK4", 80],
    ["Prusa MK4S", 80],
  ])("uses the published print power for %s", (model, watts) => {
    expect(averagePrintPower(model)?.watts).toBe(watts);
  });

  it("omits electricity when average print power is unpublished", () => {
    expect(averagePrintPower("Bambu Lab P1S")).toBeUndefined();
    expect(
      parseExpenses({ ...expenseDraft(null), acquisition_cost: "500" }, "Bambu Lab P1S"),
    ).toMatchObject({ lifetime_hours: 5000, power_watts: 0 });
  });

  it.each(["0", "123.5"])("keeps a custom average power of %s", (power) => {
    expect(
      parseExpenses(
        { ...expenseDraft(null), lifetime_hours: "2400", power_watts: power },
        "Prusa MK4S",
      ),
    ).toMatchObject({ lifetime_hours: 2400, power_watts: Number(power) });
  });

  it("leaves an empty expense draft unconfigured", () => {
    expect(parseExpenses(expenseDraft(null), "Prusa MK4")).toBeNull();
  });

  it("rejects an explicit lifetime of zero", () => {
    expect(
      expenseProblem(parseExpenses({ ...expenseDraft(null), lifetime_hours: "0" }, "Prusa MK4")),
    ).toContain("at least one print hour");
  });

  it.each(["-1", "100"])("rejects a failure rate of %s percent", (rate) => {
    expect(
      expenseProblem(
        parseExpenses({ ...expenseDraft(null), failure_rate_percent: rate }, "Prusa MK4"),
      ),
    ).toContain("Enter a failure rate");
  });
});
