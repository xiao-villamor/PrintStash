/** Model overview exposes only the canonical and sibling identities returned by authorized reads. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyMembershipSummary } from "../membership-summary";
import { aModel, aModelListItem } from "@/test-support/factories";
import { aFamilyMember, aFamilySummary } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());

describe("Family overview", () => {
  it("shows visible Family context", async () => {
    renderApp(
      <FamilyMembershipSummary
        model={aModel({ id: 2, name: "Small boat" })}
        family={aFamilySummary({ role: "rescaled" })}
      />,
      {
        routes: {
          "GET /api/v1/models/1": json(aModel({ name: "Original boat" })),
          "GET /api/v1/families/7/members": json({
            items: [
              aFamilyMember({ model: aModelListItem({ name: "Original boat" }) }),
              aFamilyMember({
                id: 12,
                model_id: 2,
                model: aModelListItem({ id: 2, name: "Small boat" }),
              }),
            ],
            total: 8,
            next_cursor: "next",
          }),
        },
      },
    );
    expect(await screen.findAllByRole("link", { name: "Original boat" })).toHaveLength(1);
    expect(screen.getByText("Rescaled")).toBeVisible();
    expect(screen.getByRole("link", { name: "Manage Family" })).toHaveAttribute(
      "href",
      "/families/7",
    );
    expect(screen.getByRole("link", { name: "View individual Models" })).toHaveAttribute(
      "href",
      "/families/7",
    );
    expect(screen.queryByRole("link", { name: "Small boat" })).not.toBeInTheDocument();
  });

  it("does not repeat the current canonical as a navigation target", async () => {
    renderApp(
      <FamilyMembershipSummary
        model={aModel({ name: "Current boat" })}
        family={aFamilySummary({ role: "canonical", member_count: 1 })}
      />,
      {
        routes: {
          "GET /api/v1/families/7/members": json({
            items: [aFamilyMember({ model: aModelListItem({ name: "Current boat" }) })],
            total: 1,
            next_cursor: null,
          }),
        },
      },
    );
    expect(await screen.findByText("1 Model")).toBeVisible();
    expect(screen.queryByRole("link", { name: "Current boat" })).not.toBeInTheDocument();
    expect(screen.getByText("Canonical", { exact: true })).toBeVisible();
  });

  it("keeps a larger Family overview to three other variations", async () => {
    renderApp(<FamilyMembershipSummary model={aModel()} family={aFamilySummary()} />, {
      routes: {
        "GET /api/v1/families/7/members": json({
          items: Array.from({ length: 5 }, (_, index) =>
            aFamilyMember({
              id: index + 10,
              model_id: index + 1,
              model: aModelListItem({ id: index + 1, name: `Boat ${index + 1}` }),
            }),
          ),
          total: 5,
          next_cursor: null,
        }),
      },
    });
    expect(await screen.findAllByRole("link", { name: /^Boat/ })).toHaveLength(3);
    expect(screen.getByRole("link", { name: "Boat 2" })).toHaveAttribute("href", "/models/2");
    expect(screen.queryByRole("link", { name: "Boat 5" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View individual Models" })).toBeVisible();
  });

  it("explains an unavailable canonical without requesting an identity", async () => {
    const { requests } = renderApp(
      <FamilyMembershipSummary
        model={aModel()}
        family={aFamilySummary({ canonical_model_id: null, member_count: 1 })}
      />,
      {
        routes: {
          "GET /api/v1/families/7/members": json({
            items: [aFamilyMember()],
            total: 1,
            next_cursor: null,
          }),
        },
      },
    );
    expect(screen.getByText("Canonical Model unavailable")).toBeVisible();
    expect(await screen.findByText("1 Model")).toBeVisible();
    expect(requests().some(({ url }) => url.startsWith("/api/v1/models/"))).toBe(false);
  });

  it("retries unavailable sibling context", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderApp(
      <FamilyMembershipSummary model={aModel({ name: "Benchy" })} family={aFamilySummary()} />,
      {
        routes: {
          "GET /api/v1/families/7/members": () =>
            failed
              ? json({ detail: "failure" }, 503)
              : json({
                  items: [
                    aFamilyMember({
                      model_id: 2,
                      model: aModelListItem({ id: 2, name: "Spatula" }),
                    }),
                  ],
                  total: 2,
                  next_cursor: null,
                }),
        },
      },
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Could not load the other variations",
    );
    failed = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("link", { name: "Spatula" })).toHaveAttribute(
      "href",
      "/models/2",
    );
  });
});
