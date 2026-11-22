/**
 * The Filters picker leads with in-place quick filters, groups the remaining
 * categories, summarizes what each is set to and clears one category alone.
 */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { LibraryFilterPicker, type LibraryFilterField } from "@/components/library-filter-picker";
import { readLibraryFilters } from "@/features/library/filters";
import { readLibraryLocation } from "@/features/library/url";
import { aModelFacets } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";

const onChange = vi.fn<(key: LibraryFilterField, values: string[]) => void>();

function renderPicker(query = "") {
  const params = new URLSearchParams(query);
  const { filters } = readLibraryFilters(params, readLibraryLocation(params), true);
  onChange.mockReset();
  renderApp(
    <LibraryFilterPicker
      filters={filters}
      facets={aModelFacets({
        material_type: [
          { value: "PLA", count: 2 },
          { value: "PETG", count: 1 },
        ],
        printed: [
          { value: "yes", count: 1 },
          { value: "no", count: 2 },
        ],
      })}
      tags={[]}
      printers={[]}
      canViewPrinters={false}
      loading={false}
      error={false}
      onRetry={vi.fn<() => void>()}
      onChange={onChange}
      onReset={vi.fn<() => void>()}
      onClose={vi.fn<() => void>()}
    />,
  );
}

describe("LibraryFilterPicker", () => {
  it.each([
    { section: "Quick filters", member: "Favorites" },
    { section: "Files and library", member: "File type" },
    { section: "Printing", member: "Material" },
  ])("lists $member under $section", ({ section, member }) => {
    renderPicker();
    const region = screen.getByRole("region", { name: section });
    expect(within(region).getByText(member)).toBeVisible();
  });

  it("toggles Favorites without leaving the category list", async () => {
    renderPicker();
    await userEvent.click(screen.getByRole("switch", { name: "Favorites" }));
    expect(onChange).toHaveBeenCalledWith("favorites", ["true"]);
    expect(screen.getByRole("region", { name: "Printing" })).toBeVisible();
  });

  it("chooses a two-state value in place", async () => {
    renderPicker();
    const printed = screen.getByRole("group", { name: "Printed" });
    await userEvent.click(within(printed).getByRole("button", { name: "Not printed" }));
    expect(onChange).toHaveBeenCalledWith("printed", ["no"]);
  });

  it("marks Any as chosen until a two-state value applies", () => {
    renderPicker("printed=yes");
    const printed = screen.getByRole("group", { name: "Printed" });
    expect(within(printed).getByRole("button", { name: "Printed" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(within(printed).getByRole("button", { name: "Any" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("clears a two-state filter through Any", async () => {
    renderPicker("has_similar_candidates=yes");
    const similar = screen.getByRole("group", { name: "Has similar candidates" });
    await userEvent.click(within(similar).getByRole("button", { name: "Any" }));
    expect(onChange).toHaveBeenCalledWith("has_similar_candidates", []);
  });

  it("summarizes the values a category is set to", () => {
    renderPicker("material_type=PLA&material_type=PETG");
    expect(screen.getByRole("button", { name: "Material: PLA, PETG" })).toBeVisible();
  });

  it("summarizes an uploaded date range", () => {
    renderPicker("uploaded_after=2026-01-01&uploaded_before=2026-02-01");
    expect(screen.getByRole("button", { name: "Uploaded: 2026-01-01 – 2026-02-01" })).toBeVisible();
  });

  it("clears only the open category", async () => {
    renderPicker("material_type=PLA&file_type=stl");
    await userEvent.click(screen.getByRole("button", { name: /^Material/ }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange.mock.calls).toEqual([["material_type", []]]);
  });

  it("clears both ends of an uploaded range", async () => {
    renderPicker("uploaded_after=2026-01-01&uploaded_before=2026-02-01");
    await userEvent.click(screen.getByRole("button", { name: /^Uploaded/ }));
    await userEvent.click(screen.getByRole("button", { name: "Clear" }));
    expect(onChange.mock.calls).toEqual([
      ["uploaded_after", []],
      ["uploaded_before", []],
    ]);
  });

  it("offers no Clear for a category without a selection", async () => {
    renderPicker();
    await userEvent.click(screen.getByRole("button", { name: /^Material/ }));
    expect(screen.queryByRole("button", { name: "Clear" })).not.toBeInTheDocument();
  });
});
