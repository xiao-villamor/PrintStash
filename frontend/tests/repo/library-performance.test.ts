/** Acceptance cannot silently omit a metric, cohort, outlier or default budget. */
import { describe, expect, it } from "vitest";
import {
  assertAcceptance,
  budgets,
  summarize,
} from "../../scripts/library-performance/budgets.mjs";

function sample(complete = 400) {
  return {
    content: 100,
    tree: 200,
    media: 300,
    complete,
    errors: [],
    phases: {
      start: 0,
      "session-validated": 50,
      cards: 100,
      tree: 200,
      media: 300,
      usable: 220,
      complete,
    },
  };
}
function cohorts() {
  return ["distributed", "dense", "deep"].flatMap((scenario) =>
    ["desktop", "mobile"].flatMap((device) =>
      ["en", "es"].map((locale) => ({
        scenario,
        device,
        locale,
        rows: Object.fromEntries(
          Object.entries(budgets).map(([kind, budget]) => [
            kind,
            Array.from({ length: budget.samples }, () => ({
              ...sample(100),
              content: 20,
              tree: 30,
              media: 40,
              phases: { ...sample().phases, cards: 20, tree: 30, media: 40, complete: 100 },
            })),
          ]),
        ),
      })),
    ),
  );
}
describe("reference host acceptance", () => {
  it("accepts every complete cohort inside the default budgets", () => {
    expect(() => assertAcceptance(cohorts())).not.toThrow();
  });
  it("rejects a missing metric", () => {
    expect(() =>
      summarize(
        Array.from({ length: 30 }, () => ({ ...sample(), media: null })),
        "warm",
      ),
    ).toThrow("missing/invalid media");
  });
  it("rejects a missing application phase", () => {
    expect(() =>
      summarize(
        Array.from({ length: 30 }, () => ({ ...sample(), phases: {} })),
        "warm",
      ),
    ).toThrow("missing phase start");
  });
  it("rejects an image failure", () => {
    expect(() =>
      summarize(
        Array.from({ length: 30 }, () => ({ ...sample(), errors: ["decode"] })),
        "warm",
      ),
    ).toThrow("failed sample");
  });
  it("rejects completion before its prerequisites", () => {
    expect(() =>
      summarize(
        Array.from({ length: 30 }, () => sample(10)),
        "warm",
      ),
    ).toThrow("completion preceded");
  });
  it("retains slow outliers in p95", () => {
    const samples = Array.from({ length: 30 }, (_, i) => sample(i > 27 ? 1200 : 400));
    expect(summarize(samples, "warm")).toMatchObject({
      n: 30,
      median: 400,
      p95: 1200,
      accepted: false,
    });
  });
  it("fails the command when a budget is exceeded", () => {
    const cases = cohorts();
    cases[0].rows.warm = Array.from({ length: 30 }, () => sample(900));
    expect(() => assertAcceptance(cases)).toThrow("Performance budgets exceeded");
  });
  it("rejects an omitted device cohort", () => {
    expect(() => assertAcceptance(cohorts().slice(1))).toThrow("Missing cohort");
  });
  it("rejects an omitted journey", () => {
    const cases = cohorts();
    cases.find((c) => c.scenario === "dense")!.rows.page = [];
    expect(() => assertAcceptance(cases)).toThrow("expected 20 samples");
  });
  it("records a functionally failed baseline without omitting its samples", () => {
    const samples = Array.from({ length: 30 }, () => ({
      ...sample(),
      complete: null,
      errors: ["unusable_tree_control"],
    }));
    expect(summarize(samples, "warm", { baseline: true })).toEqual({
      n: 30,
      failed: 30,
      median: null,
      p95: null,
      accepted: false,
    });
  });
  it("records baseline budget failures without accepting them", () => {
    expect(
      summarize(
        Array.from({ length: 30 }, () => ({ ...sample(900), phases: {} })),
        "warm",
        { baseline: true },
      ),
    ).toMatchObject({ accepted: false });
  });
});
