/* A user can turn several source files into one logical, selectable Part Group. */
import { expect, test } from "@playwright/test";

import type { ModelRead, PartGroupWrite } from "../../src/types";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Part options", () => {
  test("creates alternatives for one physical part", async ({ page }) => {
    let currentModel: ModelRead | null = null;
    let savedOptionCount = 0;
    await page.route(/\/api\/v1\/models\/1(?:\/part-options)?(?:\?.*)?$/, async (route) => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === "/api/v1/models/1/part-options" && request.method() === "PUT") {
        const payload: { groups: PartGroupWrite[] } = request.postDataJSON();
        savedOptionCount = payload.groups[0]?.options.length ?? 0;
        if (currentModel === null) {
          await route.fulfill({ status: 409, json: { detail: "model_not_loaded" } });
          return;
        }
        currentModel = {
          ...currentModel,
          part_groups: payload.groups.map((group, groupIndex) => ({
            id: groupIndex + 1,
            name: group.name,
            options: group.options.map((option, optionIndex) => ({
              id: optionIndex + 1,
              file_id: option.file_id,
              name: option.name,
              is_default: option.is_default ?? false,
            })),
          })),
        };
        await route.fulfill({ json: currentModel });
        return;
      }
      if (pathname !== "/api/v1/models/1") {
        await route.continue();
        return;
      }

      const response = await route.fetch();
      const model: ModelRead = await response.json();
      const first = { ...model.files[0], tags: [] };
      currentModel = {
        ...model,
        files: [
          first,
          {
            ...first,
            id: 4,
            original_filename: "skadis_kitchen-roll_screw_long.stl",
            version: 3,
            sha256: "4".repeat(64),
          },
          { ...model.files[1], tags: [] },
        ],
        part_groups: [],
      };
      await route.fulfill({ json: currentModel });
    });

    await page.goto("/models/1");
    await page.getByRole("tab", { name: "Files" }).click();
    await page.getByRole("button", { name: "Manage options" }).click();
    await page.getByRole("button", { name: "Add part" }).click();
    await page.getByPlaceholder("e.g. Handle").fill("Handle length");
    await page.getByRole("button", { name: "Save options" }).click();

    await expect(page.getByText("Handle length", { exact: true })).toBeVisible();
    await expect(page.getByText("Default", { exact: true })).toBeVisible();
    expect(savedOptionCount).toBe(2);
  });
});
