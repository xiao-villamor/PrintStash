/*
 * The facet panel: eight filter groups over whatever the library actually holds.
 *
 * Every option here comes from a facet count the server computed under the
 * *current* filters, which is what makes the numbers beside each option mean
 * anything. A group rendered from a hard-coded list instead would offer values
 * that match nothing and hide values that do — and the user has no way to tell
 * an empty result from a filter that was never available.
 *
 * Selection is multi-value and additive within a group: picking PLA and then
 * PETG means "either", not "PETG instead". Getting that wrong turns every second
 * click into a silent replacement of the first.
 *
 * The two failure states are deliberately different from each other. While the
 * counts are loading the groups still render, because collapsing them would make
 * the sidebar jump under the cursor; when the request failed the panel says so,
 * because silently showing zero options reads as "you have nothing", which is a
 * lie about the library rather than about the request.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { StructuredFilters } from "@/components/structured-filters";
import type { ModelFacetsRead } from "@/types";

type Props = Parameters<typeof StructuredFilters>[0];

/**
 * The rendered label for the `file_type` group.
 *
 * The panel sits inside `<Localized>`, which rewrites UI text through the
 * product's own vocabulary — "File type" reaches the DOM as "Artifact", the term
 * `CONTEXT.md` makes binding. Matching the source string would pass only until
 * someone corrected the wording, which is the opposite of what this defends.
 */
const FILE_TYPE_GROUP = "Artifact";

const FACETS: ModelFacetsRead = {
  file_type: [
    { value: "stl", count: 12 },
    { value: "gcode", count: 4 },
  ],
  material_type: [{ value: "PLA", count: 9 }],
  slicer_name: [{ value: "OrcaSlicer", count: 3 }],
  printer_model: [],
  revision_status: [{ value: "known_good", count: 2 }],
  print_outcome: [],
  storage: [{ value: "vault", count: 12 }],
  printed: [],
};

function renderFilters(over: Partial<Props> = {}) {
  const onChange = vi.fn<Props["onChange"]>();
  const onDateChange = vi.fn<NonNullable<Props["onDateChange"]>>();
  const onClearAll = vi.fn<NonNullable<Props["onClearAll"]>>();
  const result = render(
    <StructuredFilters
      facets={FACETS}
      active={{}}
      onChange={onChange}
      onDateChange={onDateChange}
      onClearAll={onClearAll}
      {...over}
    />,
  );
  return { ...result, onChange, onDateChange, onClearAll };
}

describe("StructuredFilters", () => {
  describe("what it offers", () => {
    it("lists the values the library actually holds", () => {
      renderFilters();

      expect(screen.getByText("stl")).toBeInTheDocument();
      expect(screen.getByText("PLA")).toBeInTheDocument();
    });

    it("shows how many models each value matches", () => {
      // The count is what makes a facet worth clicking; without it the user is
      // guessing which filter narrows anything.
      renderFilters();

      expect(screen.getByText("12")).toBeInTheDocument();
    });

    it("leaves out a group the library has nothing for", () => {
      renderFilters();

      expect(screen.queryByText("Printer model")).toBeNull();
    });
  });

  describe("choosing values", () => {
    it("reports the value the user picked", async () => {
      const user = userEvent.setup();
      const { onChange } = renderFilters();

      await user.click(screen.getByText("stl"));

      expect(onChange).toHaveBeenCalledWith("file_type", ["stl"]);
    });

    it("adds a second value to the first rather than replacing it", async () => {
      // Picking PLA and then PETG means "either". Replacing turns every second
      // click into a silent undo of the first.
      const user = userEvent.setup();
      const { onChange } = renderFilters({ active: { file_type: ["stl"] } });

      await user.click(screen.getByText("gcode"));

      expect(onChange).toHaveBeenCalledWith("file_type", ["stl", "gcode"]);
    });

    it("removes a value the user unpicks", async () => {
      const user = userEvent.setup();
      const { onChange } = renderFilters({ active: { file_type: ["stl", "gcode"] } });

      await user.click(screen.getByText("stl"));

      expect(onChange).toHaveBeenCalledWith("file_type", ["gcode"]);
    });
  });

  describe("collapsing a group", () => {
    it("hides a group's values when it is collapsed", async () => {
      const user = userEvent.setup();
      renderFilters();

      await user.click(screen.getByRole("button", { name: FILE_TYPE_GROUP }));

      expect(screen.queryByText("stl")).toBeNull();
    });

    it("brings them back when it is expanded again", async () => {
      const user = userEvent.setup();
      renderFilters();
      await user.click(screen.getByRole("button", { name: FILE_TYPE_GROUP }));

      await user.click(screen.getByRole("button", { name: FILE_TYPE_GROUP }));

      expect(screen.getByText("stl")).toBeInTheDocument();
    });

    it("starts a rarely-used group collapsed", () => {
      // Eight groups expanded at once is a sidebar nobody can scan.
      renderFilters();

      expect(screen.queryByText("OrcaSlicer")).toBeNull();
    });
  });

  describe("the upload date window", () => {
    it("reports the start of the window", async () => {
      const user = userEvent.setup();
      const { onDateChange } = renderFilters();

      await user.type(screen.getByLabelText("After"), "2026-01-01");

      expect(onDateChange).toHaveBeenCalledWith("uploaded_after", "2026-01-01");
    });

    it("reports the end of the window", async () => {
      const user = userEvent.setup();
      const { onDateChange } = renderFilters();

      await user.type(screen.getByLabelText("Before"), "2026-02-01");

      expect(onDateChange).toHaveBeenCalledWith("uploaded_before", "2026-02-01");
    });

    it("counts an active date window as a filter to clear", async () => {
      const user = userEvent.setup();
      const { onClearAll } = renderFilters({ uploadedAfter: "2026-01-01" });

      await user.click(screen.getByRole("button", { name: /Clear/ }));

      expect(onClearAll).toHaveBeenCalledTimes(1);
    });

    it("clears each group itself when the caller offers no handler", async () => {
      // The panel owns the reset when nothing above it does, so a consumer that
      // only wires `onChange` still gets a working Clear.
      const user = userEvent.setup();
      const { onChange } = renderFilters({
        active: { file_type: ["stl"] },
        onClearAll: undefined,
      });

      await user.click(screen.getByRole("button", { name: /Clear/ }));

      expect(onChange).toHaveBeenCalledWith("file_type", []);
    });
  });

  describe("clearing", () => {
    it("offers to clear everything once something is active", async () => {
      const user = userEvent.setup();
      const { onClearAll } = renderFilters({ active: { file_type: ["stl"] } });

      await user.click(screen.getByRole("button", { name: /Clear/ }));

      expect(onClearAll).toHaveBeenCalledTimes(1);
    });

    it("offers nothing to clear when no filter is active", () => {
      renderFilters();

      expect(screen.queryByRole("button", { name: /Clear/ })).toBeNull();
    });
  });

  describe("while the counts are unavailable", () => {
    it("says the values are still coming", async () => {
      renderFilters({ facets: undefined, loading: true });

      expect(await screen.findByText("Loading filter values…")).toBeInTheDocument();
    });

    it("keeps an already-active filter visible while they load", async () => {
      // The group is the only way back out of the filter, so it survives a
      // reload of the counts even though its options have not arrived.
      renderFilters({ facets: undefined, loading: true, active: { file_type: ["stl"] } });

      // The badge carrying the selected count joins the accessible name.
      expect(
        await screen.findByRole("button", { name: new RegExp(FILE_TYPE_GROUP) }),
      ).toBeInTheDocument();
    });

    it("says so when the counts could not be fetched", () => {
      // Silently showing zero options reads as "you have nothing", which is a
      // lie about the library rather than about the request.
      renderFilters({ facets: undefined, error: true });

      expect(screen.getByText("Filter values could not be loaded.")).toBeInTheDocument();
    });
  });
});
