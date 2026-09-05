/** Unified library groups multipart compositions without taking ownership of source Models. */
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

const populatedMobileDetail: MultipartModelRead = {
  id: 90,
  name: "broom_holder_vcd_base_25mm",
  slug: "broom-holder-vcd-base-25mm",
  description: "A multipart holder with a fixed base and an alternative body.",
  collection: "tetitas",
  collection_id: 1,
  part_count: 1,
  model_count: 2,
  guide_count: 0,
  cover_model_id: 1,
  cover_image_url: null,
  cover_image_uploaded: false,
  cover_thumbnail_url: null,
  starred: false,
  member_model_ids: [1, 2],
  tags: ["demo"],
  effective_role: "admin",
  created_at: "2026-06-04T00:24:22.000000",
  updated_at: "2026-06-04T00:24:22.000000",
  parts: [
    {
      id: 1,
      name: "broom_holder_vcd_base_25mm",
      quantity: 1,
      sort_order: 0,
      models: candidates.slice(0, 2).map((candidate, index) => ({
        ...candidate,
        choice_id: index + 1,
      })),
    },
  ],
  guides: [],
};

test.describe("multipart models", () => {
  test("keeps populated editor controls in the mobile flow", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/v1/multipart-models/90", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ json: populatedMobileDetail });
        return;
      }
      await route.continue();
    });

    await page.goto("/multipart-models/90");
    await page.getByRole("button", { name: "Edit multipart set" }).click();

    const addVariant = page.getByRole("button", { name: "Add variant" });
    const saveChanges = page.getByRole("button", { name: "Save changes" });
    const controls = [
      addVariant,
      page.getByRole("textbox", { name: "Name", exact: true }),
      saveChanges,
    ];
    const bounds = await Promise.all(controls.map((control) => control.boundingBox()));
    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    const partOverflow = await page
      .getByRole("group", { name: "broom_holder_vcd_base_25mm" })
      .evaluate((part) => part.scrollWidth - part.clientWidth);
    const [saveBox, guidesBox] = await Promise.all([
      page.getByRole("button", { name: "Save changes" }).boundingBox(),
      page
        .getByRole("heading", { name: "Guides", exact: true })
        .locator("xpath=ancestor::section[1]")
        .boundingBox(),
    ]);

    expect(horizontalOverflow).toBeLessThanOrEqual(0);
    expect(partOverflow).toBeLessThanOrEqual(0);
    for (const box of bounds) {
      expect(box).not.toBeNull();
      expect(box!.x).toBeGreaterThanOrEqual(0);
      expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    }
    expect((await addVariant.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    expect((await saveChanges.boundingBox())!.height).toBeGreaterThanOrEqual(44);
    expect(saveBox).not.toBeNull();
    expect(guidesBox).not.toBeNull();
    expect(saveBox!.y).toBeGreaterThanOrEqual(guidesBox!.y + guidesBox!.height);
  });

  test("preserves Models while creating fixed parts with alternatives", async ({ page }) => {
    let savedPayload: {
      name: string;
      description: string | null;
      collection_id: number | null;
      cover_model_id: number | null;
      cover_image_url: string | null;
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
      guide_count: 0,
      cover_model_id: null,
      cover_image_url: null,
      cover_image_uploaded: false,
      cover_thumbnail_url: null,
      starred: false,
      member_model_ids: [],
      tags: [],
      effective_role: "admin",
      created_at: "2026-06-04T00:24:22.000000",
      updated_at: "2026-06-04T00:24:22.000000",
      parts: [],
      guides: [],
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
        const payload = request.postDataJSON() as {
          name: string;
          description: string | null;
          collection_id: number | null;
        };
        detail = {
          ...detail,
          name: payload.name,
          description: payload.description,
          collection_id: payload.collection_id,
          collection: payload.collection_id === 1 ? "maraio" : null,
        };
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
          collection_id: number | null;
          cover_model_id: number | null;
          cover_image_url: string | null;
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
          collection_id: payload.collection_id,
          collection: payload.collection_id === 1 ? "maraio" : null,
          cover_model_id: payload.cover_model_id,
          cover_image_url: payload.cover_image_url,
          cover_thumbnail_url: payload.cover_image_url,
          parts: payload.parts.map((part, index) => ({
            id: index + 1,
            name: part.name,
            quantity: 1,
            sort_order: index,
            models: part.choices.map((choice, choiceIndex) => ({
              ...candidates.find((candidate) => candidate.id === choice.model_id)!,
              choice_id: choice.choice_id ?? choiceIndex + 1,
            })),
          })),
          part_count: payload.parts.length,
          model_count: payload.parts.reduce((count, part) => count + part.choices.length, 0),
          member_model_ids: [
            ...new Set(
              payload.parts.flatMap((part) => part.choices.map((choice) => choice.model_id)),
            ),
          ],
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
    await page.getByRole("button", { name: "New multipart set" }).first().click();
    await page.getByLabel("Name", { exact: true }).fill("Desk organiser");
    await page.getByLabel("Description").fill("A complete desk organiser");
    await page.getByLabel("Collection").selectOption({ label: "maraio" });
    await page.getByRole("button", { name: "Create multipart set" }).click();

    await expect(page.getByRole("heading", { name: "Desk organiser" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Edit multipart set" })).toBeVisible();
    await page.setViewportSize({ width: 390, height: 844 });
    const horizontalOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(horizontalOverflow).toBeLessThanOrEqual(0);
    await page.getByRole("button", { name: "Add a part" }).click();
    await page.getByRole("button", { name: /Desk base/ }).click();
    await page.getByRole("button", { name: "Add another part" }).click();
    await page.getByRole("button", { name: /Short handle/ }).click();
    await page.locator("fieldset").nth(1).getByRole("button", { name: "Add variant" }).click();
    await page.getByRole("button", { name: /Long handle/ }).click();
    await page
      .getByRole("textbox", { name: "Or use an image URL" })
      .fill("https://images.example.test/desk-organiser.webp");
    await page.getByRole("button", { name: "Save changes" }).click();

    await expect(page.getByText("Changes saved")).toBeVisible();
    expect(savedPayload).toMatchObject({
      collection_id: 1,
      cover_image_url: "https://images.example.test/desk-organiser.webp",
      parts: [
        { name: "Part 1", choices: [{ model_id: 2 }] },
        { name: "Part 2", choices: [{ model_id: 3 }, { model_id: 4 }] },
      ],
    });
    await expect(page.getByText("Choose one").first()).toBeVisible();
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByLabel("Description").fill("Print the base before attaching the handle.");
    await page.getByRole("button", { name: "Save changes" }).click();
    expect(savedPayload).toMatchObject({
      collection_id: 1,
      description: "Print the base before attaching the handle.",
    });
    await expect(page.getByText("Print the base before attaching the handle.")).toBeVisible();
    const cardDimensions = await page.getByRole("link", { name: /Desk base/ }).evaluate((card) => {
      const bounds = card.getBoundingClientRect();
      return { height: bounds.height, width: bounds.width };
    });
    expect(Math.abs(cardDimensions.width - cardDimensions.height)).toBeLessThanOrEqual(1);
    expect(cardDimensions.width).toBeLessThanOrEqual(288);
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(page.getByRole("button", { name: "Edit multipart set" })).toBeVisible();
    await expect(page.getByRole("link", { name: /Desk base/ })).toBeVisible();
    await page.setViewportSize({ width: 1280, height: 720 });
    await page.goto("/?c=maraio&type=multipart");
    await expect(page.getByRole("heading", { name: "maraio" })).toBeVisible();
    await expect(
      page.getByRole("button", { name: "Multipart sets only", exact: true }).first(),
    ).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByRole("link", { name: /Desk organiser/ })).toBeVisible();
    const setCardDimensions = await page
      .getByRole("link", { name: /Desk organiser/ })
      .evaluate((card) => {
        const bounds = card.getBoundingClientRect();
        return { height: bounds.height, width: bounds.width };
      });
    expect(Math.abs(setCardDimensions.width - setCardDimensions.height)).toBeLessThanOrEqual(1);
    expect(setCardDimensions.width).toBeLessThanOrEqual(340);
    await page.getByRole("button", { name: "Everything", exact: true }).first().click();
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();

    await page.goto("/multipart-models/90");
    await page.getByRole("button", { name: "Edit multipart set" }).click();
    await page.getByRole("button", { name: "Delete multipart set" }).click();
    await expect(page.getByRole("dialog")).toContainText("Models, files and revisions stay");
    await page.getByRole("button", { name: "Delete set" }).click();
    await expect(page).toHaveURL(/\?c=maraio$/);
    await expect(page.getByText("skadis_kitchen-roll_screw").first()).toBeVisible();
  });
});
