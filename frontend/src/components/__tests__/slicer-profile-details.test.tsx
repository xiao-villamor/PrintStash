/** Profiles expose grouped read-only observations with paginated source evidence. */
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ProfileSettingsDetails } from "@/components/slicer-profile-details";
import { slicerProfileKeys } from "@/lib/queries/slicer-profiles";
import { aSlicerCapture, anEditingBase } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { SlicerProfileRead } from "@/types/slicer-profiles";

const profile = { id: 1, name: "Everyday PLA", ...anEditingBase() };
const key = slicerProfileKeys.profile(
  "filament",
  profile.id,
  profile.edit_epoch,
  profile.edit_version,
);
const route = "GET /api/v1/slicer-profiles/filament/1";
function renderDetails(
  items: SlicerProfileRead[] = [aSlicerCapture()],
  next: number | null = null,
) {
  return renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
    seed: [[key, { pages: [{ items, next_before_id: next }], pageParams: [null] }]],
  });
}
async function openDetails() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Slicer settings and variants" }));
  return user;
}

describe("ProfileSettingsDetails", () => {
  it("shows source files inside the profile", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    expect(screen.getByRole("link", { name: "bracket.gcode · Bracket" })).toHaveAttribute(
      "href",
      "/models/4",
    );
  });
  it("preserves multiple filament values", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    expect(screen.getByText("0.98;1.02")).toBeVisible();
  });
  it("filters settings by value", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    await user.type(screen.getAllByRole("textbox", { name: "Find a setting or value" })[0], "0.98");
    expect(screen.getByText("filament_flow_ratio")).toBeVisible();
    expect(screen.queryByText("filament_type")).not.toBeInTheDocument();
  });
  it("explains an empty search result", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    await user.type(
      screen.getAllByRole("textbox", { name: "Find a setting or value" })[0],
      "missing",
    );
    expect(screen.getByText("No matching settings were captured in this group.")).toBeVisible();
  });
  it("shows an empty profile", async () => {
    renderDetails([]);
    await openDetails();
    expect(
      screen.getByText("No settings found for this profile's material and brand or printer model."),
    ).toBeVisible();
  });
  it("keeps process settings with their file", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    await user.click(screen.getByText("Print settings for this file"));
    expect(screen.getByText("layer_height")).toBeVisible();
  });
  it("groups sources loaded on later pages", async () => {
    renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
      seed: [
        [key, { pages: [{ items: [aSlicerCapture()], next_before_id: 10 }], pageParams: [null] }],
      ],
      routes: {
        [route]: json({
          items: [aSlicerCapture({ file_id: 9, filename: "older.gcode" })],
          next_before_id: null,
        }),
      },
    });
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "Load more source files" }));
    expect(await screen.findByText("Variants: 1 · Loaded files: 2")).toBeVisible();
    expect(screen.getAllByRole("article")).toHaveLength(1);
    await user.click(screen.getByRole("button", { name: "View settings" }));
    expect(screen.getByRole("link", { name: "older.gcode · Bracket" })).toBeVisible();
    expect(screen.getByRole("link", { name: "bracket.gcode · Bracket" })).toBeVisible();
  });
  it("offers retry after a failed read", async () => {
    renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
      routes: { [route]: json({ detail: "unavailable" }, 503) },
    });
    await openDetails();
    expect(
      within(await screen.findByRole("alert")).getByRole("button", { name: "Retry" }),
    ).toBeVisible();
  });
  it("refreshes observations", async () => {
    renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
      seed: [[key, { pages: [{ items: [], next_before_id: null }], pageParams: [null] }]],
      routes: { [route]: json({ items: [aSlicerCapture()], next_before_id: null }) },
    });
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    expect(await screen.findByText("Variants: 1 · Loaded files: 1")).toBeVisible();
  });
  it("hides cached observations after access is denied", async () => {
    renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
      seed: [
        [key, { pages: [{ items: [aSlicerCapture()], next_before_id: null }], pageParams: [null] }],
      ],
      routes: { [route]: json({ detail: "access_denied" }, 403) },
    });
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await screen.findByRole("alert");
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("does not claim empty success after a failed read", async () => {
    renderApp(<ProfileSettingsDetails kind="filament" profile={profile} />, {
      routes: { [route]: json({ detail: "unavailable" }, 503) },
    });
    await openDetails();
    await screen.findByRole("alert");
    expect(
      screen.queryByText(
        "No settings found for this profile's material and brand or printer model.",
      ),
    ).not.toBeInTheDocument();
  });
  it("offers continuation through empty source pages", async () => {
    renderDetails([], 10);
    await openDetails();
    expect(screen.getByText("No captured settings in the loaded files yet.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Load more source files" })).toBeVisible();
  });
  it("collapses the profile details", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "Hide slicer settings" }));
    expect(screen.queryByRole("article")).not.toBeInTheDocument();
  });
  it("collapses a variant", async () => {
    renderDetails();
    const user = await openDetails();
    await user.click(screen.getByRole("button", { name: "View settings" }));
    await user.click(screen.getByRole("button", { name: "Hide settings" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("link", { name: "bracket.gcode · Bracket" }),
      ).not.toBeInTheDocument(),
    );
  });
});
