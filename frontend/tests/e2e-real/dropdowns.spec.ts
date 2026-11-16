/**
 * Dropdown placement is observable only in a browser with the real page layout.
 * Statistics and a view-only Model put their triggers near the phone screen's
 * left edge; every option must remain reachable without horizontal scrolling.
 */
import { test as authenticatedTest, expect, authBundleFor, authedContext } from "./helpers";
import { modelCard, uploadModel } from "./util";

const test = authenticatedTest.extend({
  page: async ({ page }, use) => {
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => {
      if (message.text().startsWith("Native browser error:")) errors.push(message.text());
    });
    await page.addInitScript(() => {
      window.addEventListener("error", (event) => {
        console.error("Native browser error: " + event.message);
      });
    });
    await use(page);
    await page.evaluate(
      () =>
        new Promise<void>((resolve) =>
          requestAnimationFrame(() => requestAnimationFrame(() => resolve())),
        ),
    );
    expect(errors).toEqual([]);
  },
});

test.describe("dropdown viewport placement", () => {
  test("keeps Statistics customization reachable on a phone", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/statistics");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    const panel = page.getByRole("dialog");
    await expect(panel).toHaveAttribute("data-state", "open");
    const bounds = await panel.boundingBox();
    expect(bounds).not.toBeNull();
    expect(bounds!.x).toBeGreaterThanOrEqual(0);
    expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    const workload = panel.getByRole("checkbox", { name: "Printer workload" });
    await workload.uncheck();
    await page.keyboard.press("Escape");
    await page.reload();
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    await expect(page.getByRole("checkbox", { name: "Printer workload" })).not.toBeChecked();
  });

  test("repositions an open dropdown after a viewport resize", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/statistics");
    await page.getByRole("button", { name: "Customize", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    const panel = page.getByRole("dialog");
    await expect(async () => {
      const bounds = await panel.boundingBox();
      expect(bounds).not.toBeNull();
      expect(bounds!.x).toBeGreaterThanOrEqual(0);
      expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
    }).toPass();
    await panel.getByRole("checkbox", { name: "Top collections" }).uncheck();
    await expect(panel.getByRole("checkbox", { name: "Top collections" })).not.toBeChecked();
  });

  for (const resized of [false, true]) {
    test(`keeps dropdown options above page chrome in a short viewport (${resized ? "resized" : "direct"})`, async ({
      page,
    }) => {
      await page.setViewportSize(
        resized ? { width: 1440, height: 1000 } : { width: 390, height: 260 },
      );
      await page.goto("/statistics");
      const trigger = page.getByRole("button", { name: "Customize", exact: true });
      await trigger.click();
      if (resized) await page.setViewportSize({ width: 390, height: 260 });
      const panel = page.getByRole("dialog");
      await expect(panel).toHaveAttribute("data-state", "open");
      await expect(async () => {
        const bounds = await panel.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.y).toBeGreaterThanOrEqual(0);
        expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(260);
      }).toPass();
      for (const name of ["Most printed models", "Top collections"]) {
        const option = panel.getByRole("checkbox", { name });
        await option.scrollIntoViewIfNeeded();
        await expect(async () => {
          expect(
            await option.evaluate((element) => {
              const bounds = element.getBoundingClientRect();
              return (
                document.elementFromPoint(
                  bounds.x + bounds.width / 2,
                  bounds.y + bounds.height / 2,
                ) === element
              );
            }),
          ).toBe(true);
        }).toPass({ timeout: 3000 });
        const original = await option.isChecked();
        await option.setChecked(!original);
        await expect(option).toBeChecked({ checked: !original });
        await option.setChecked(original);
      }
      await page.keyboard.press("Escape");
      await expect(trigger).toBeFocused();
      await expect(panel).toHaveCount(0);
    });
  }

  test("keeps a member Model action menu inside the phone screen", async ({ page, browser }) => {
    const stamp = Date.now();
    const name = `dropdown-model-${stamp}`;
    const username = `dropdown-viewer-${stamp}`;
    const password = "test-password-123";
    const collectionResponse = await page.request.post("/api/v1/collections", {
      data: { name: `dropdown-collection-${stamp}` },
    });
    expect(collectionResponse.ok()).toBeTruthy();
    const collection: { id: number; name: string } = await collectionResponse.json();
    const userResponse = await page.request.post("/api/v1/admin/users", {
      data: { username, password },
    });
    expect(userResponse.ok()).toBeTruthy();
    const user: { id: number } = await userResponse.json();
    let modelId: string | undefined;
    try {
      await uploadModel(page, name, { collection: collection.name });
      await modelCard(page, name).click();
      await expect(page.getByRole("heading", { name, exact: true })).toBeVisible();
      modelId = new URL(page.url()).pathname.split("/").at(-1);
      const grant = await page.request.put(
        `/api/v1/collections/${collection.id}/permissions/${user.id}`,
        {
          data: { role: "view" },
        },
      );
      expect(grant.ok()).toBeTruthy();
      const member = await authedContext(browser, await authBundleFor(username, password));
      try {
        await member.page.setViewportSize({ width: 390, height: 844 });
        await member.page.goto(`/models/${modelId}`);
        const trigger = member.page.getByRole("heading", { level: 1 });
        await trigger.click({ button: "right" });
        const panel = member.page.getByRole("menu");
        await expect(panel).toHaveAttribute("data-state", "open");
        const bounds = await panel.boundingBox();
        expect(bounds).not.toBeNull();
        expect(bounds!.x).toBeGreaterThanOrEqual(0);
        expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
        await expect(panel.getByRole("menuitem", { name: "Delete model" })).toBeVisible();
        await member.page.keyboard.press("Escape");
        await expect(trigger).toBeFocused();
      } finally {
        await member.context.close();
      }
    } finally {
      if (modelId) {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).ok()).toBeTruthy();
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).ok()).toBeTruthy();
      }
      expect((await page.request.delete(`/api/v1/collections/${collection.id}`)).ok()).toBeTruthy();
      expect((await page.request.delete(`/api/v1/admin/users/${user.id}`)).ok()).toBeTruthy();
    }
  });
});
