/** Family navigation resolves readable slugs while retaining existing numeric links. */
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import ModelFamilyPage from "../model-family";
import { aFamily } from "@/test-support/families";
import { json, renderApp } from "@/test-support/render";

afterEach(() => vi.unstubAllGlobals());
describe("Family route", () => {
  it.each(["benchy-variations", "7"])("resolves the authorized %s identity", async (identity) => {
    renderApp(<ModelFamilyPage />, {
      at: `/families/${identity}`,
      routePath: "/families/:id",
      routes: {
        "GET /api/v1/families/by-slug/benchy-variations": json(aFamily()),
        "GET /api/v1/families/7": json(aFamily({ canonical_model_id: null })),
        "GET /api/v1/families/7/members": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/tags": json([]),
      },
    });
    expect(await screen.findByRole("heading", { name: "Benchy variations" })).toBeVisible();
  });

  it.each(["missing-family", "0", "9007199254740992"])(
    "shows not found for %s",
    async (identity) => {
      renderApp(<ModelFamilyPage />, { at: `/families/${identity}`, routePath: "/families/:id" });
      expect(await screen.findByRole("heading", { name: "404" })).toBeVisible();
    },
  );

  it("retries a temporarily unavailable slug lookup", async () => {
    const user = userEvent.setup();
    let failed = true;
    renderApp(<ModelFamilyPage />, {
      at: "/families/benchy-variations",
      routePath: "/families/:id",
      routes: {
        "GET /api/v1/families/by-slug/benchy-variations": () =>
          failed ? json({ detail: "failure" }, 503) : json(aFamily()),
        "GET /api/v1/families/7": json(aFamily({ canonical_model_id: null })),
        "GET /api/v1/families/7/members": json({ items: [], total: 0, next_cursor: null }),
        "GET /api/v1/tags": json([]),
      },
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    failed = false;
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByRole("heading", { name: "Benchy variations" })).toBeVisible();
  });
});
