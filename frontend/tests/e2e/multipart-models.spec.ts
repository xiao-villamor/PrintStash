/** Standalone multipart composition stays understandable without hiding source Models. */
import { expect, test } from "@playwright/test";

import { useMockApi } from "./_setup";
import type { MultipartModelCandidate, MultipartModelRead } from "../../src/types";

useMockApi();

const candidates: MultipartModelCandidate[] = [
  {
    id: 1,
    name: "skadis_kitchen-roll_screw",
    slug: "skadis-kitchen-roll-screw",
    thumbnail_url: null,
    source_file_count: 1,
    gcode_revision_count: 1,
    available: true,
  },
  {
    id: 2,
    name: "Desk base",
    slug: "desk-base",
    thumbnail_url: null,
    source_file_count: 1,
    gcode_revision_count: 2,
    available: true,
  },
  {
    id: 3,
    name: "Short handle",
    slug: "short-handle",
    thumbnail_url: null,
    source_file_count: 1,
    gcode_revision_count: 1,
    available: true,
  },
  {
    id: 4,
    name: "Long handle",
    slug: "long-handle",
    thumbnail_url: null,
    source_file_count: 1,
    gcode_revision_count: 3,
    available: true,
  },
];

test.describe("multipart models", () => {
  test("preserves Models while creating fixed parts with alternatives", async ({ page }) => {
    let savedPayload: {
      name: string;
      description: string | null;
      parts: Array<{ name: string; choices: Array<{ model_id: number; choice_id?: number }> }>;
    } | null = null;
    let detail: MultipartModelRead = {
      id: 90,
      name: "Desk organiser",
      slug: "desk-organiser",
      description: "A complete desk organiser",
      collection: null,
      collection_id: null,
      part_count: 0,
      model_count: 0,
      cover_thumbnail_url: null,
      effective_role: "admin",
      created_at: "2026-06-04T00:24:22.000000",
      updated_at: "2026-06-04T00:24:22.000000",
      parts: [],
    };

    await page.route("**/api/v1/multipart-models**", async (route) => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.pathname === "/api/v1/multipart-models" && request.method() === "GET") {
        await route.fulfill({ json: detail.part_count ? [detail] : [] });
        return;
      }
      if (url.pathname === "/api/v1/multipart-models" && request.method() === "POST") {
        // SAFETY: the browser sends this exact JSON shape from NewMultipartModelModal.
        const payload = request.postDataJSON() as { name: string; description: string | null };
        detail = { ...detail, name: payload.name, description: payload.description };
        await route.fulfill({ json: detail });
        return;
      }
      if (url.pathname === "/api/v1/multipart-models/90/candidates") {
        await route.fulfill({ json: candidates });
        return;
      }
      if (url.pathname === "/api/v1/multipart-models/90" && request.method() === "GET") {
        await route.fulfill({ json: detail });
        return;
      }
      if (url.pathname === "/api/v1/multipart-models/90" && request.method() === "PUT") {
        // SAFETY: the browser sends the typed atomic MultipartPartsWrite payload.
        const payload = request.postDataJSON() as {
          name: string;
          description: string | null;
          parts: Array<{
            name: string;
            choices: Array<{ model_id: number; choice_id?: number }>;
          }>;
        };
        savedPayload = payload;
        detail = {
          ...detail,
          name: payload.name,
          description: payload.description,
          parts: payload.parts.map((part, index) => ({
            id: index + 1,
            name: part.name,
            sort_order: index,
            models: part.choices.map((choice, choiceIndex) => ({
              ...candidates.find((candidate) => candidate.id === choice.model_id)!,
              choice_id: choice.choice_id ?? choiceIndex + 1,
            })),
          })),
          part_count: payload.parts.length,
          model_count: payload.parts.reduce((count, part) => count + part.choices.length, 0),
        };
        await route.fulfill({ json: detail });
        return;
      }
      if (url.pathname === "/api/v1/multipart-models/90" && request.method() === "DELETE") {
        detail = { ...detail, part_count: 0, model_count: 0 };
        await route.fulfill({ status: 204 });
        return;
      }
      await route.continue();
    });

    await page.goto("/");
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
    await page.getByRole("tab", { name: "Multipart models" }).click();
    await page.getByRole("button", { name: "New multipart model" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill("Desk organiser");
    await page.getByLabel("Description").fill("A complete desk organiser");
    await page.getByRole("button", { name: "Create multipart model" }).click();

    await expect(page.getByRole("heading", { name: "Desk organiser" })).toBeVisible();
    await page.getByRole("button", { name: "Add a part" }).first().click();
    await page.getByRole("button", { name: /Desk base/ }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: /Short handle/ }).click();
    await page
      .locator("fieldset")
      .nth(1)
      .getByRole("button", { name: "Add an alternative" })
      .click();
    await page.getByRole("button", { name: /Long handle/ }).click();
    await page.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByText("Changes saved")).toBeVisible();
    expect(savedPayload).toMatchObject({
      parts: [
        { name: "Part 1", choices: [{ model_id: 2 }] },
        { name: "Part 2", choices: [{ model_id: 3 }, { model_id: 4 }] },
      ],
    });
    await expect(page.getByText("Choose one").first()).toBeVisible();

    await page.goto("/?v=multipart");
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

    await page.goto("/multipart-models/90");
    await page.getByRole("button", { name: "Delete multipart model" }).click();
    await expect(page.getByRole("dialog")).toContainText("Models, files and revisions stay");
    await page.getByRole("button", { name: "Delete grouping" }).click();
    await expect(page).toHaveURL(/\?v=multipart/);
    await page.getByRole("tab", { name: "Models", exact: true }).click();
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  });
});
