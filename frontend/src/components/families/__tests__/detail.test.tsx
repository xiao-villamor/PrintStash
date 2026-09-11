/** Family views expose independent Models and only explicitly scoped actions. */
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyDetail } from "../detail";
import { FamilyComparison } from "../comparison";
import { aModel, aModelListItem } from "@/test-support/factories";
import { aFamily, aFamilyMember } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

describe("Family detail", () => {
  it("compares refreshed member metadata after a canonical change", async () => {
    const user = userEvent.setup();
    let updated = false;
    const first = aFamilyMember();
    const second = aFamilyMember({
      id: 12,
      model_id: 2,
      role: "identical",
      model: aModelListItem({ id: 2, name: "Small boat" }),
    });
    const { client } = renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7": json(aFamily()),
        "GET /api/v1/models/1": json(aModel()),
        "GET /api/v1/tags": json([]),
        "GET /api/v1/families/7/members": () =>
          json({
            items: updated
              ? [
                  { ...first, role: "rescaled", scale_factor: 2 },
                  { ...second, role: "canonical", scale_factor: 1 },
                ]
              : [first, second],
            total: 2,
            next_cursor: null,
          }),
      },
    });
    await user.click(
      await screen.findByRole("checkbox", { name: "Select for comparison: Benchy" }),
    );
    await user.click(screen.getByRole("checkbox", { name: "Select for comparison: Small boat" }));
    updated = true;
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["families", 7, "members"] });
    });
    await user.click(screen.getByRole("button", { name: "Compare two Models (2/2)" }));
    const table = screen.getByRole("table");
    expect(within(table).getByRole("row", { name: "Variation Rescaled Canonical" })).toBeVisible();
    expect(within(table).getByRole("row", { name: "Scale 2 1" })).toBeVisible();
    // The instrumented refetch + dialog scenario measures about 4.5s by itself;
    // allow the same scenario to complete when full-suite workers share the CPU.
  }, 10_000);
  it("shows member metadata without combining Revisions", async () => {
    const member = aFamilyMember({
      gcode_revision_count: 4,
      known_good_count: 2,
      source_file_count: 3,
      formats: ["stl", "gcode"],
      latest_print_outcome: "completed",
      transformation_note: "Strengthened bridge",
    });
    renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7/members": json({ items: [member], total: 1, next_cursor: null }),
        "GET /api/v1/families/7": json(aFamily()),
        "GET /api/v1/models/1": json(aModel({ name: "Benchy" })),
        "GET /api/v1/tags": json([]),
      },
    });
    const card = await screen.findByRole("article", { name: "Benchy" });
    expect(within(card).getByText("STL / GCODE", { exact: false })).toBeVisible();
    expect(within(card).getByText("G-code Revisions").nextElementSibling).toHaveTextContent("4");
    expect(within(card).getByText("Known-good Revisions").nextElementSibling).toHaveTextContent(
      "2",
    );
    expect(within(card).getByText("Strengthened bridge")).toBeVisible();
    expect(within(card).getByRole("link", { name: "Benchy" })).toHaveAttribute("href", "/models/1");
  });

  it.each(["en", "es"] as const)("explains a canonical vacancy in %s", async (locale) => {
    renderApp(<FamilyDetail id={7} />, {
      locale,
      routes: {
        "GET /api/v1/families/7/members": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/families/7": json(
          aFamily({ canonical_model_id: null, canonical_member_id: null }),
        ),
        "GET /api/v1/tags": json([]),
      },
    });
    const label = locale === "en" ? "Print canonical Model" : "Imprimir Modelo canónico";
    expect(await screen.findByRole("button", { name: label })).toBeDisabled();
    expect(
      screen.getByText(
        locale === "en" ? "Canonical Model unavailable" : "Modelo canónico no disponible",
      ),
    ).toBeVisible();
    expect(
      await screen.findByText(
        locale === "en"
          ? "No Models match these filters."
          : "Ningún Modelo coincide con estos filtros.",
      ),
    ).toBeVisible();
  });

  it("requires exactly two selected members to compare", async () => {
    const user = userEvent.setup();
    const members = [
      aFamilyMember(),
      aFamilyMember({
        id: 12,
        model_id: 2,
        model: aModelListItem({ id: 2, name: "Small boat" }),
        role: "rescaled",
      }),
      aFamilyMember({
        id: 13,
        model_id: 3,
        model: aModelListItem({ id: 3, name: "Large boat" }),
        role: "print_variant",
      }),
    ];
    renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7/members": json({ items: members, total: 3, next_cursor: null }),
        "GET /api/v1/families/7": json(aFamily()),
        "GET /api/v1/models/1": json(aModel()),
        "GET /api/v1/tags": json([]),
      },
    });
    const compare = await screen.findByRole("button", { name: "Compare two Models" });
    expect(compare).toBeDisabled();
    await user.click(
      await screen.findByRole("checkbox", { name: "Select for comparison: Benchy" }),
    );
    expect(compare).toBeDisabled();
    await user.click(screen.getByRole("checkbox", { name: "Select for comparison: Small boat" }));
    expect(compare).toBeEnabled();
    expect(
      screen.getByRole("checkbox", { name: "Select for comparison: Large boat" }),
    ).toBeDisabled();
    await user.click(compare);
    expect(await screen.findByRole("dialog", { name: "Compare two Models" })).toBeVisible();
  });

  it("filters members on the server", async () => {
    const user = userEvent.setup();
    const { requestsWithMethod } = renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7/members": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/families/7": json(aFamily()),
        "GET /api/v1/models/1": json(aModel()),
        "GET /api/v1/tags": json([]),
      },
    });
    await user.selectOptions(await screen.findByLabelText("Variation"), "rescaled");
    await user.click(screen.getByText("More filters"));
    await user.selectOptions(screen.getByLabelText("Formats"), "3mf");
    await user.selectOptions(screen.getByLabelText("Known-good Revisions"), "true");
    await user.selectOptions(screen.getByLabelText("Sort members"), "scale-asc");
    await waitFor(() =>
      expect(
        requestsWithMethod("GET").some(
          ({ url }) =>
            url.includes("role=rescaled") &&
            url.includes("file_type=3mf") &&
            url.includes("known_good=true") &&
            url.includes("sort=scale-asc"),
        ),
      ).toBe(true),
    );
  });

  it("offers no bulk Model deletion", async () => {
    const user = userEvent.setup();
    renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7/members": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/families/7": json(aFamily()),
        "GET /api/v1/models/1": json(aModel()),
        "GET /api/v1/tags": json([]),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Family actions" }));
    expect(screen.getByRole("menuitem", { name: "Move Family to trash" })).toBeVisible();
    expect(
      screen.queryByRole("menuitem", { name: /delete.*models|trash.*members/i }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("menuitem", { name: "Move Family to trash" }));
    expect(screen.getByRole("dialog")).toHaveTextContent(
      "All Models, files and print history remain",
    );
  });

  it("hides editing when a reserved member is read-only", async () => {
    renderApp(<FamilyDetail id={7} />, {
      routes: {
        "GET /api/v1/families/7/members": json({
          items: [aFamilyMember()],
          total: 1,
          next_cursor: null,
        }),
        "GET /api/v1/families/7": json(aFamily({ effective_role: "view" })),
        "GET /api/v1/models/1": json(aModel()),
        "GET /api/v1/tags": json([]),
      },
    });
    expect(await screen.findByText(/Editing requires edit access/)).toBeVisible();
    expect(screen.queryByRole("button", { name: "Add member" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Actions for Benchy" })).not.toBeInTheDocument();
  });
});

describe("Family metadata comparison", () => {
  it("keeps metadata readable for unsupported previews", () => {
    const left = aFamilyMember({ preview_file: null, formats: ["step"], gcode_revision_count: 3 });
    const right = aFamilyMember({
      id: 12,
      model_id: 2,
      model: aModelListItem({ id: 2, name: "Spatula" }),
      preview_file: null,
      formats: ["gcode"],
      gcode_revision_count: 1,
      role: "repaired",
    });
    renderApp(<FamilyComparison members={[left, right]} />);
    expect(screen.getAllByText(/3D preview unavailable/)).toHaveLength(2);
    expect(screen.getByRole("columnheader", { name: "Benchy" })).toBeVisible();
    expect(screen.getByRole("columnheader", { name: "Spatula" })).toBeVisible();
    expect(screen.getByRole("row", { name: "G-code Revisions 3 1" })).toBeVisible();
    expect(screen.getByText(/STL and OBJ do not declare units/)).toBeVisible();
  });
});
