/** Preparation names each indexing step and its state; the index check reports what verification established. */
import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  SearchIndexHealth,
  SearchPreparationStages,
} from "@/components/search-preparation-progress";
import { renderApp } from "@/test-support/render";
import { aSearchGeneration } from "@/test-support/search";

/** A step's readable text; its position badge is hidden from assistive technology. */
function label(item: HTMLElement) {
  return item.textContent?.replace(/^\d/, "");
}
function steps() {
  return within(screen.getByRole("list", { name: "Preparation steps" }))
    .getAllByRole("listitem")
    .map(label);
}

describe("SearchPreparationStages", () => {
  it("marks earlier steps done while passages are indexed", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "backfill" })}
      />,
    );
    expect(steps()).toEqual([
      "Check library content(done)",
      "Index passages(in progress)",
      "Catch up on recent changes(not started)",
      "Verify the index(not started)",
    ]);
  });
  it("names the running step as the current one", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "backfill" })}
      />,
    );
    const current = within(screen.getByRole("list", { name: "Preparation steps" }))
      .getAllByRole("listitem")
      .filter((item) => item.getAttribute("aria-current") === "step");
    expect(current.map(label)).toEqual(["Index passages(in progress)"]);
  });
  it("explains passage indexing with the model doing it", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({
          state: "building",
          phase: "backfill",
          model: "bge-small-en-v1.5",
        })}
      />,
    );
    expect(screen.getByText(/bge-small-en-v1\.5 turns each passage into a vector/)).toBeVisible();
  });
  it("starts with the library content check", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "reconcile" })}
      />,
    );
    expect(steps()[0]).toBe("Check library content(in progress)");
    expect(screen.getByText(/reading every Model/)).toBeVisible();
  });
  it("shows the catch-up step running after indexing", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "final_reconcile" })}
      />,
    );
    expect(steps()).toEqual([
      "Check library content(done)",
      "Index passages(done)",
      "Catch up on recent changes(in progress)",
      "Verify the index(not started)",
    ]);
  });
  it("shows verification running", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "verify" })}
      />,
    );
    expect(steps()[3]).toBe("Verify the index(in progress)");
    expect(screen.getByText(/running a test search/)).toBeVisible();
  });
  it("marks verification as needing attention after a failed check", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "verify_failed" })}
      />,
    );
    expect(steps()[3]).toBe("Verify the index(needs attention)");
    expect(screen.getByText(/did not pass its checks/)).toBeVisible();
  });
  it("marks every step done once the index is verified", () => {
    renderApp(
      <SearchPreparationStages
        generation={aSearchGeneration({ state: "building", phase: "ready" })}
      />,
    );
    expect(steps().every((step) => step?.endsWith("(done)"))).toBe(true);
    expect(screen.getByText(/Every check passed/)).toBeVisible();
  });
  it("rejects a phase a preparing index cannot be in", () => {
    expect(() =>
      renderApp(
        <SearchPreparationStages
          generation={aSearchGeneration({ state: "building", phase: "draining" })}
        />,
      ),
    ).toThrow("search_generation_phase_invalid");
  });
});

describe("SearchIndexHealth", () => {
  function value(label: string) {
    const term = within(screen.getByRole("region", { name: "Index check" })).getByText(label);
    return term.nextElementSibling?.textContent;
  }
  it("reports every searchable entry indexed", () => {
    renderApp(
      <SearchIndexHealth generation={aSearchGeneration({ eligible: 567, indexed: 567 })} />,
    );
    expect(value("Searchable entries")).toBe("567 of 567 indexed");
  });
  it("reports entries the index is still missing", () => {
    renderApp(<SearchIndexHealth generation={aSearchGeneration({ eligible: 10, indexed: 7 })} />);
    expect(value("Searchable entries")).toBe("7 of 10 indexed");
  });
  it("reports entries waiting for a retry", () => {
    renderApp(<SearchIndexHealth generation={aSearchGeneration({ quarantined: 3 })} />);
    expect(value("Waiting for a retry")).toBe("3");
  });
  it("reports entries shortened to fit the model", () => {
    renderApp(<SearchIndexHealth generation={aSearchGeneration({ truncated_count: 2 })} />);
    expect(value("Shortened to fit the model")).toBe("2");
  });
  it("names the model that built the index", () => {
    renderApp(<SearchIndexHealth generation={aSearchGeneration({ model: "bge-small-en-v1.5" })} />);
    expect(value("AI model")).toBe("bge-small-en-v1.5");
  });
  it("reports when the last check passed", () => {
    renderApp(
      <SearchIndexHealth generation={aSearchGeneration({ verified_at: "2026-09-11T12:01:00Z" })} />,
    );
    expect(value("Last check")).toMatch(/^Passed /);
  });
  it("reports an index that has not been checked", () => {
    renderApp(<SearchIndexHealth generation={aSearchGeneration({ verified_at: null })} />);
    expect(value("Last check")).toBe("Not checked yet");
  });
});
