/** Collapsed browsing preserves the server's mixed pages and personal Family actions. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FamilyBrowseGrid } from "../browse";
import { aModelListItem } from "@/test-support/factories";
import { aFamily } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
describe("Collapsed Family browsing", () => {
  it("appends the server's mixed pages without regrouping Models", async () => {
    const user = userEvent.setup();
    renderApp(<FamilyBrowseGrid params={{}} />, {
      routes: {
        "GET /api/v1/families/browse": (url) =>
          json(
            url.includes("cursor=next")
              ? {
                  items: [{ kind: "model", model: aModelListItem({ name: "Loose cube" }) }],
                  total: 2,
                  next_cursor: null,
                }
              : {
                  items: [
                    {
                      kind: "family",
                      family: aFamily({ matching_visible_members: 1, total_visible_members: 3 }),
                    },
                  ],
                  total: 2,
                  next_cursor: "next",
                },
          ),
      },
    });
    expect(await screen.findByRole("heading", { name: "Benchy variations" })).toBeVisible();
    expect(screen.getByText("1 of 3 Models match")).toBeVisible();
    expect(screen.getByRole("link", { name: "View individual Models" })).toHaveAttribute(
      "href",
      "/?family_id=7&browse=models",
    );
    await user.click(screen.getByRole("button", { name: "Load more" }));
    expect(await screen.findByText("Loose cube")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Benchy variations" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("stars only the selected Family", async () => {
    const user = userEvent.setup();
    let starred = false;
    const { requestsWithMethod } = renderApp(<FamilyBrowseGrid params={{}} />, {
      routes: {
        "GET /api/v1/families/browse": () =>
          json({
            items: [{ kind: "family", family: aFamily({ starred }) }],
            total: 1,
            next_cursor: null,
          }),
        "PUT /api/v1/families/7/star": () => {
          starred = true;
          return json(aFamily({ starred }));
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Star Family" }));
    expect(await screen.findByRole("button", { name: "Unstar Family" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(requestsWithMethod("PUT").map(({ url }) => url)).toEqual(["/api/v1/families/7/star"]);
  });

  it("keeps the personal star unchanged after rejection", async () => {
    const user = userEvent.setup();
    renderApp(<FamilyBrowseGrid params={{}} />, {
      routes: {
        "GET /api/v1/families/browse": json({
          items: [{ kind: "family", family: aFamily() }],
          total: 1,
          next_cursor: null,
        }),
        "PUT /api/v1/families/7/star": json({ detail: "family_permission_denied" }, 403),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Star Family" }));
    expect(await screen.findByText(/edit access/i)).toBeVisible();
    expect(screen.getByRole("button", { name: "Star Family" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("retries a failed mixed page", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderApp(<FamilyBrowseGrid params={{}} />, {
      routes: {
        "GET /api/v1/families/browse": () =>
          failed
            ? json({ detail: "failure" }, 503)
            : json({ items: [], total: 0, next_cursor: null }),
      },
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    failed = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No Models match these filters.")).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
  });
});
