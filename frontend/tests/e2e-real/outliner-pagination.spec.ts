/** A late tree page remains navigable and draggable through the real API. */
import { execFileSync } from "node:child_process";
import path from "node:path";
import { test, expect } from "./helpers";

test.describe("outliner pagination", () => {
  test("moves a model reached through keyboard pagination beyond 500 entries", async ({ page }) => {
    const backend = path.resolve("../backend");
    const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR ?? path.resolve("tests/e2e-real/.data");
    const seeded = JSON.parse(
      execFileSync(
        path.join(backend, ".venv/bin/python"),
        ["-m", "tests.fakes.outliner_library", dataRoot],
        {
          cwd: backend,
          encoding: "utf8",
        },
      ),
    );
    try {
      await page.goto("/");
      const sidebar = page.locator("aside");
      const root = sidebar.getByRole("button", { name: seeded.root.name, exact: true });
      await root.locator("..").getByRole("button", { name: "Expand", exact: true }).click();
      const folder = sidebar.getByRole("button", { name: seeded.folder.name, exact: true });
      await folder.locator("..").getByRole("button", { name: "Expand", exact: true }).click();
      const more = sidebar.getByRole("button", { name: "Show more models" });
      for (let i = 0; i < 10; i++) {
        await expect(more).toHaveAttribute("aria-disabled", "false");
        await more.focus();
        await Promise.all([
          page.waitForResponse(
            (response) => new URL(response.url()).pathname === "/api/v1/outliner/entries",
          ),
          page.keyboard.press("Enter"),
        ]);
        if (i < 9) await expect(more).toHaveAttribute("aria-disabled", "false");
      }
      const target = sidebar.getByRole("button", { name: seeded.target.name, exact: true });
      await expect(target).toBeVisible();
      await expect(sidebar.getByRole("button", { name: "All items loaded" })).toBeFocused();
      await target.press("Enter");
      await expect(page).toHaveURL(new RegExp(`/models/${seeded.target.id}$`));

      await page.goto("/");
      await expect(target).toHaveCount(0);
      for (let i = 0; i < 10; i++) {
        await expect(more).toHaveAttribute("aria-disabled", "false");
        await Promise.all([
          page.waitForResponse(
            (response) => new URL(response.url()).pathname === "/api/v1/outliner/entries",
          ),
          more.click(),
        ]);
        if (i < 9) await expect(more).toHaveAttribute("aria-disabled", "false");
      }
      const destination = sidebar.getByRole("button", {
        name: seeded.destination.name,
        exact: true,
      });
      await target.scrollIntoViewIfNeeded();
      const from = await target.boundingBox();
      if (!from) throw new Error("missing drag source");
      await page.mouse.move(from.x + from.width / 2, from.y + from.height / 2);
      await page.mouse.down();
      await page.mouse.move(from.x + 10, from.y - 10, { steps: 4 });
      await destination.scrollIntoViewIfNeeded();
      const to = await destination.boundingBox();
      if (!to) throw new Error("missing drop destination");
      await page.mouse.move(to.x + to.width / 2, to.y + to.height / 2, { steps: 10 });
      await page.mouse.up();
      await expect
        .poll(async () => {
          const response = await page.request.get(`/api/v1/models/${seeded.target.id}`);
          return (await response.json()).collection;
        })
        .toBe(seeded.destination.path);

      // Search finds the relocated model even though its destination has never opened.
      await sidebar.getByPlaceholder("Filter outliner...").fill("Zebra paginated target");
      await expect(
        sidebar.getByRole("button", { name: seeded.target.name, exact: true }),
      ).toBeVisible();
      await sidebar.getByTitle("Open location").click();
      await expect(page).toHaveURL(new RegExp(`c=${encodeURIComponent(seeded.destination.path)}`));
    } finally {
      expect(
        (
          await page.request.delete(`/api/v1/collections/${seeded.root.id}?recursive=true`)
        ).status(),
      ).toBe(204);
    }
  });
});
