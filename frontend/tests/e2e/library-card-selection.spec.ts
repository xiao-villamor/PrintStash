/** Card selection stays quiet while browsing, discoverable by pointer or keyboard, and usable on touch. */
import { expect, test } from "@playwright/test";
import { aCollectionNode, aModelListItem } from "../../src/test-support/factories";
import { useMockApi } from "./_setup";

useMockApi();

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/collections/children?**", (route) =>
    route.fulfill({
      json: {
        items: [
          aCollectionNode({ id: 50, name: "Workshop", path: "workshop", display_path: "Workshop" }),
        ],
        next_cursor: null,
      },
    }),
  );
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: [
          aModelListItem({ id: 1, name: "Alpha" }),
          aModelListItem({ id: 2, name: "Beta" }),
        ].map((model) => ({ kind: "model", model })),
        total: 2,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
});

test.describe("Library card selection", () => {
  test.use({ viewport: { width: 1440, height: 900 } });

  for (const kind of ["Model", "folder"] as const) {
    const label = kind === "Model" ? "Select Alpha" : "Select folder Workshop";
    test(`hides an idle ${kind} checkbox`, async ({ page }) => {
      await page.goto("/");
      await page.mouse.move(0, 0);
      const control = page.getByRole("checkbox", { name: label, exact: true }).locator("..");
      await expect(control).toHaveCSS("opacity", "0");
      await expect(control).toHaveCSS("pointer-events", "none");
    });

    test(`reveals only the hovered ${kind} checkbox`, async ({ page }, testInfo) => {
      await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
      await page.goto("/");
      const main = page.getByRole("main");
      const card =
        kind === "Model"
          ? main.getByRole("link", { name: /Alpha/ })
          : main.getByRole("button", { name: /Workshop/ });
      const control = page.getByRole("checkbox", { name: label, exact: true }).locator("..");
      const other = page.getByRole("checkbox", { name: "Select Beta", exact: true }).locator("..");
      await card.hover();
      await expect(control).toHaveCSS("opacity", "1");
      await expect(other).toHaveCSS("opacity", "0");
      await page.screenshot({
        path: testInfo.outputPath(`selection-${kind}-hover.png`),
        animations: "disabled",
      });
      await page.mouse.move(0, 0);
      await expect(control).toHaveCSS("opacity", "0");
      await page.screenshot({
        path: testInfo.outputPath(`selection-${kind}-idle.png`),
        animations: "disabled",
      });
    });

    test(`reveals a ${kind} checkbox on keyboard focus`, async ({ page }) => {
      await page.goto("/");
      const checkbox = page.getByRole("checkbox", { name: label, exact: true });
      await checkbox.focus();
      await expect(checkbox.locator("..")).toHaveCSS("opacity", "1");
      await checkbox.press("Space");
      await expect(checkbox).toBeChecked();
      await expect(page).toHaveURL((url) => url.pathname === "/");
    });
  }

  test("keeps all card checkboxes visible during selection", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("main").getByRole("link", { name: /Alpha/ }).hover();
    await page.getByRole("checkbox", { name: "Select Alpha", exact: true }).click();
    await page.getByRole("button", { name: "Done", exact: true }).focus();
    await page.mouse.move(0, 0);
    for (const label of ["Select Alpha", "Select Beta", "Select folder Workshop"]) {
      await expect(
        page.getByRole("checkbox", { name: label, exact: true }).locator(".."),
      ).toHaveCSS("opacity", "1");
    }
  });

  test("restores quiet cards after finishing selection", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("main").getByRole("link", { name: /Alpha/ }).hover();
    await page.getByRole("checkbox", { name: "Select Alpha", exact: true }).click();
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await page.mouse.move(0, 0);
    for (const label of ["Select Alpha", "Select Beta", "Select folder Workshop"]) {
      const checkbox = page.getByRole("checkbox", { name: label, exact: true });
      await expect(checkbox).not.toBeChecked();
      await expect(checkbox.locator("..")).toHaveCSS("opacity", "0");
    }
  });

  test.describe("touch input", () => {
    test.use({ hasTouch: true, viewport: { width: 390, height: 900 } });
    test("retains a visible tap target", async ({ page }) => {
      await page.goto("/");
      const checkbox = page.getByRole("checkbox", { name: "Select folder Workshop", exact: true });
      await expect(checkbox.locator("..")).toHaveCSS("opacity", "1");
      await checkbox.tap();
      await expect(checkbox).toBeChecked();
      await expect(page.getByRole("button", { name: "Done", exact: true })).toBeVisible();
    });
  });
});

test("converts implied selection into the other loaded models", async ({ page }) => {
  await page.route("**/api/v1/models/browse?**", (route) =>
    route.fulfill({
      json: {
        items: [
          aModelListItem({ id: 1, name: "Alpha", collection: "workshop", collection_id: 50 }),
          aModelListItem({ id: 2, name: "Beta", collection: "workshop/nested", collection_id: 51 }),
          aModelListItem({ id: 3, name: "Gamma", collection: "workshop-other", collection_id: 52 }),
        ].map((model) => ({ kind: "model", model })),
        total: 3,
        next_cursor: null,
        browse_revision: "r1",
        authorization_revision: "a1",
      },
    }),
  );
  await page.goto("/");
  const folder = page.getByRole("checkbox", { name: "Select folder Workshop", exact: true });
  await folder.focus();
  await folder.press("Space");
  const alpha = page.getByRole("checkbox", { name: "Select Alpha", exact: true });
  const beta = page.getByRole("checkbox", { name: "Select Beta", exact: true });
  const gamma = page.getByRole("checkbox", { name: "Select Gamma", exact: true });
  await expect(alpha).toBeChecked();
  await expect(beta).toBeChecked();
  await expect(gamma).not.toBeChecked();
  await alpha.click();
  await expect(folder).not.toBeChecked();
  await expect(alpha).not.toBeChecked();
  await expect(beta).toBeChecked();
});
