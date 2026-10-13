/** Real Library navigation keeps its connection and restores the originating entry's reading position. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

test.describe("Library navigation", () => {
  test("reuses the admitted shell through real Model detail navigation", async ({ page }) => {
    const name = `e2e-nav-connection-${Date.now()}`;
    await uploadGcodeModel(page, name);
    let sockets = 0;
    let probes = 0;
    page.on("websocket", (socket) => {
      if (new URL(socket.url()).pathname === "/api/v1/events/ws") sockets += 1;
    });
    page.on("request", (request) => {
      if (new URL(request.url()).pathname === "/api/v1/setup/status") probes += 1;
    });
    try {
      // ── A fresh page load opens the connection and is admitted ─────────────
      await page.goto("/");
      await expect(modelCard(page, name)).toBeVisible();
      await expect.poll(() => sockets).toBe(1);
      await page.waitForLoadState("networkidle");
      const admitted = probes;
      expect(admitted).toBeGreaterThan(0);

      // ── Detail and Back are application paths: nothing is reopened ─────────
      await modelCard(page, name).click();
      await expect(page.getByRole("heading", { name })).toBeVisible();
      await page.getByRole("link", { name: "Back", exact: true }).click();
      await expect(modelCard(page, name)).toBeVisible();
      await page.waitForLoadState("networkidle");

      expect(sockets).toBe(1);
      expect(probes).toBe(admitted);
    } finally {
      const found = await (
        await page.request.get(`/api/v1/models?q=${encodeURIComponent(name)}`)
      ).json();
      for (const model of found.items ?? found) {
        expect((await page.request.delete(`/api/v1/models/${model.id}`)).status()).toBe(204);
      }
    }
  });

  for (const { layout, width, height } of [
    { layout: "grid", width: 1280, height: 900 },
    { layout: "list", width: 390, height: 844 },
  ] as const) {
    test(`restores a paged Library reading position through real detail navigation (${layout})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      await page.addInitScript((value) => localStorage.setItem("ps-vault-view", value), layout);
      const backend = path.resolve("../backend");
      const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR ?? path.resolve("tests/e2e-real/.data");
      const seeded = JSON.parse(
        execFileSync(
          path.join(backend, ".venv/bin/python"),
          ["-m", "tests.fakes.outliner_library", dataRoot],
          { cwd: backend, encoding: "utf8" },
        ),
      );
      try {
        await page.goto(`/?c=${encodeURIComponent(seeded.folder.path)}&type=all&sort=name-asc`);
        const entries = page.locator("main [data-library-entry]");
        await expect(entries.first()).toBeVisible();
        const firstPageCount = await entries.count();
        await page.getByRole("button", { name: "Load more", exact: true }).click();
        await expect.poll(() => entries.count()).toBeGreaterThan(firstPageCount + 10);
        const target = entries.nth(firstPageCount + 10);
        const targetHref = await target.getAttribute("data-library-entry");
        if (!targetHref) throw new Error("Missing paged reading anchor");
        const link = page.locator(`main [data-library-entry="${targetHref}"]`);
        await link.scrollIntoViewIfNeeded();
        const before = await link.evaluate((node) => ({
          top: node.getBoundingClientRect().top,
          offsets: Array.from(document.querySelectorAll("main, main .overflow-y-auto")).map(
            (element) => element.scrollTop,
          ),
        }));
        expect(Math.max(...before.offsets)).toBeGreaterThan(100);
        const origin = page.url();
        const originKey = await page.evaluate(() => history.state.key);
        const box = await link.boundingBox();
        if (!box) throw new Error("Missing visible reading anchor");
        await page.mouse.click(box.x + 20, box.y + 20);
        await expect(page).toHaveURL(new RegExp(`${targetHref}\\?return=`));
        await page.getByRole("link", { name: "Back", exact: true }).click();

        await expect(page).toHaveURL(origin);
        expect(await page.evaluate(() => history.state.key)).toBe(originKey);
        await expect
          .poll(() => link.evaluate((node) => node.getBoundingClientRect().top))
          .toBeCloseTo(before.top, 0);
        expect(
          await page
            .locator("main, main .overflow-y-auto")
            .evaluateAll((nodes) => nodes.map((node) => node.scrollTop)),
        ).toEqual(before.offsets);
        await page.goForward();
        await expect(page).toHaveURL(new RegExp(`${targetHref}\\?return=`));
        await page.goBack();
        await expect(page).toHaveURL(origin);
        await expect
          .poll(() => link.evaluate((node) => node.getBoundingClientRect().top))
          .toBeCloseTo(before.top, 0);
      } finally {
        expect(
          (
            await page.request.delete(`/api/v1/collections/${seeded.root.id}?recursive=true`)
          ).status(),
        ).toBe(204);
      }
    });
  }
});
