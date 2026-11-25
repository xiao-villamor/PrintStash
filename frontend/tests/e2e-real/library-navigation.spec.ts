/** Real Library navigation keeps its connection and restores the originating entry's reading position. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "./helpers";
import { scrollToLibraryEntry } from "../e2e/_scroll";
import { settleLibraryRead } from "../e2e/_library";
import { openModelPage, modelCard, uploadGcodeModel } from "./util";
import type { LibraryAuthority, LibraryBrowsePage } from "../../src/types/library-browse";

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
      await settleLibraryRead(page, modelCard(page, name));
      await expect.poll(() => sockets).toBeGreaterThan(0);
      await page.waitForLoadState("networkidle");
      const admitted = probes;
      const connected = sockets;
      expect(admitted).toBeGreaterThan(0);

      // ── Detail and Back are application paths: nothing is reopened ─────────
      await openModelPage(page, name);
      await expect(page.getByRole("heading", { name })).toBeVisible();
      await page.getByRole("link", { name: "Back", exact: true }).click();
      await expect(modelCard(page, name)).toBeVisible();
      await page.waitForLoadState("networkidle");

      expect(sockets).toBe(connected);
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

  for (const { viewport, width, height } of [
    { viewport: "desktop", width: 1280, height: 900 },
    { viewport: "mobile", width: 390, height: 844 },
  ] as const) {
    test(`restores a paged Library reading position through real detail navigation (${viewport})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      const backend = path.resolve("../backend");
      const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR ?? path.resolve("tests/e2e-real/.data");
      const seeded = JSON.parse(
        execFileSync(
          path.join(backend, ".venv/bin/python"),
          ["-m", "tests.fakes.outliner_library", dataRoot, "--models", "100"],
          { cwd: backend, encoding: "utf8" },
        ),
      );
      try {
        await page.goto(`/?c=${encodeURIComponent(seeded.folder.path)}&type=all&sort=name-asc`);
        const entries = page.locator("main [data-library-entry]");
        await settleLibraryRead(page, entries.first());
        const link = page
          .getByRole("main")
          .getByRole("link", { name: new RegExp(seeded.target.name) })
          .first();
        await scrollToLibraryEntry(page, link);
        const targetHref = await link.getAttribute("data-library-entry");
        if (!targetHref) throw new Error("Missing paged reading anchor");
        const before = await link.evaluate((node) => ({
          top: node.getBoundingClientRect().top,
          offsets: Array.from(document.querySelectorAll("main, main .overflow-y-auto")).map(
            (element) => element.scrollTop,
          ),
        }));
        expect(Math.max(...before.offsets)).toBeGreaterThan(100);
        const origin = page.url();
        const originKey = await page.evaluate(() => history.state.__TSR_key);
        const box = await link.boundingBox();
        if (!box) throw new Error("Missing visible reading anchor");
        // The upper-left corner is the selection control; navigate through
        // the visible card body without scrolling the captured reading anchor.
        await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
        await page
          .locator("#model-drawer")
          .getByRole("link", { name: "Open page", exact: true })
          .click();
        await expect(page).toHaveURL(new RegExp(`${targetHref}\\?return=`));
        await page.getByRole("link", { name: "Back", exact: true }).click();

        await expect(page).toHaveURL(origin);
        expect(await page.evaluate(() => history.state.__TSR_key)).toBe(originKey);
        await expect
          .poll(() => link.evaluate((node) => node.getBoundingClientRect().top))
          .toBeCloseTo(before.top, 0);
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

    test(`preserves a paged reading position during automatic refresh (${viewport})`, async ({
      page,
    }) => {
      await page.setViewportSize({ width, height });
      const backend = path.resolve("../backend");
      const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR ?? path.resolve("tests/e2e-real/.data");
      const seeded = JSON.parse(
        execFileSync(
          path.join(backend, ".venv/bin/python"),
          ["-m", "tests.fakes.outliner_library", dataRoot, "--models", "100"],
          { cwd: backend, encoding: "utf8" },
        ),
      );
      const tag = await page.request.post("/api/v1/tags", {
        data: { name: `Revision change ${seeded.root.id}-${Date.now()}` },
      });
      expect(tag.status()).toBe(201);
      const changedTag: { id: number } = await tag.json();
      try {
        const firstRead = page.waitForResponse((response) => {
          const url = new URL(response.url());
          return (
            url.pathname === "/api/v1/models/browse" &&
            !url.searchParams.has("cursor") &&
            response.status() === 200
          );
        });
        await page.goto(`/?c=${encodeURIComponent(seeded.folder.path)}&type=all&sort=name-asc`);
        const first: LibraryBrowsePage = await (await firstRead).json();
        expect(first.next_cursor).not.toBeNull();
        // Traverse real virtual rows: DOM node counts are only the visible window.
        const last = page
          .getByRole("main")
          .getByRole("link", { name: new RegExp(seeded.target.name) })
          .first();
        await scrollToLibraryEntry(page, last);
        await expect(last).toBeVisible();
        const before = await page.getByRole("main").evaluate((main) => {
          const scroll = main;
          const bounds = scroll.getBoundingClientRect();
          const anchor = Array.from(
            main.querySelectorAll<HTMLElement>("[data-library-entry]"),
          ).find((node) => {
            const rect = node.getBoundingClientRect();
            return rect.bottom > bounds.top && rect.top < bounds.bottom;
          });
          if (!anchor?.dataset.libraryEntry) throw new Error("Missing visible reading anchor");
          return {
            key: anchor.dataset.libraryEntry,
            top: anchor.getBoundingClientRect().top,
            offset: scroll.scrollTop,
          };
        });
        expect(before.offset).toBeGreaterThan(100);
        await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");
        const beforeChange: LibraryAuthority = await (
          await page.request.get("/api/v1/models/browse/revision")
        ).json();
        const freshRead = page.waitForResponse(async (response) => {
          const url = new URL(response.url());
          if (
            url.pathname !== "/api/v1/models/browse" ||
            url.searchParams.has("cursor") ||
            response.status() !== 200
          )
            return false;
          const result: LibraryBrowsePage = await response.json();
          return result.browse_revision !== beforeChange.browse_revision;
        });

        expect((await page.request.delete(`/api/v1/tags/${changedTag.id}`)).status()).toBe(204);
        // The regular foreground revision poll notices the persisted write.
        await freshRead;
        await expect(page.getByRole("main")).toHaveAttribute("aria-busy", "false");

        const anchor = page.locator(`main [data-library-entry="${before.key}"]`).first();
        await expect
          .poll(async () =>
            Math.abs(
              (await anchor.evaluate((node) => node.getBoundingClientRect().top)) - before.top,
            ),
          )
          .toBeLessThanOrEqual(1);
        await expect(
          page.getByRole("button", { name: "Refresh library", exact: true }),
        ).toBeHidden();
        await expect(
          page.getByText(
            "Your previous reading position could not be restored. Showing the start of this view.",
            { exact: true },
          ),
        ).toBeHidden();
        await expect(last).toBeVisible();
        await page.screenshot({ path: test.info().outputPath("automatic-refresh.png") });
      } finally {
        const cleanup = await page.request.delete(`/api/v1/tags/${changedTag.id}`);
        expect([204, 404]).toContain(cleanup.status());
        expect(
          (
            await page.request.delete(`/api/v1/collections/${seeded.root.id}?recursive=true`)
          ).status(),
        ).toBe(204);
      }
    });
  }
});
