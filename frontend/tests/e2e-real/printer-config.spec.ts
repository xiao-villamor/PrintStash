/** Saving a configuration file must target the selected source, preserve its bytes and surface concurrent changes. */
import { expect, test } from "./helpers";
import { collapseNavigationSidebar } from "./util";

const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;
const emulator = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`;
const filePath = "includes/disposable.cfg";
const original = "# Disposable emulator configuration\r\n\r\n";

test.describe("Moonraker configuration files", () => {
  test("persists exact nested config edits across reload", async ({ page }, testInfo) => {
    const created = await page.request.post(`${api}/printers`, {
      data: { name: `Config editor ${Date.now()}`, moonraker_url: emulator },
    });
    expect(created.ok()).toBe(true);
    const { id }: { id: number } = await created.json();
    const file = `${api}/printers/${id}/config/file?${new URLSearchParams({ path: filePath })}`;
    const defaultSource = `${emulator}/server/files/config/printer.cfg`;
    const defaultContent = await (await page.request.get(defaultSource)).text();
    try {
      await page.goto(`/printers/${id}?tab=config`);
      await collapseNavigationSidebar(page);
      const source = page.getByRole("navigation", { name: "Configuration file browser" });
      await source.getByRole("button", { name: "disposable.cfg", exact: true }).click();
      const editorName = `${filePath} Configuration text`;
      const editor = page.getByRole("textbox", { name: editorName, exact: true });
      await expect(page).toHaveURL((url) => url.searchParams.get("file") === filePath);
      await expect(editor).toHaveValue(original.replaceAll("\r", ""));
      await expect(page.getByRole("heading", { name: "Configuration files" })).toBeVisible();
      await expect(page.locator("html")).toHaveJSProperty("scrollWidth", 1920);
      await page.screenshot({ path: testInfo.outputPath("config-editor.png") });
      await editor.fill("# Reverted draft");
      await page.getByRole("button", { name: "Revert edits", exact: true }).click();
      await expect(editor).toHaveValue(original.replaceAll("\r", ""));

      // A rejected file switch and a rejected route change both retain the draft.
      const typed = "# Edited in Playwright café  \n\n";
      await editor.fill(typed);
      await source.getByRole("button", { name: "read-only.conf", exact: true }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
      await page.getByRole("tab", { name: "Status", exact: true }).click();
      await page.getByRole("dialog").getByRole("button", { name: "Cancel" }).click();
      await expect(editor).toHaveValue(typed);
      await expect(page.getByText("Unsaved edits", { exact: true })).toBeVisible();

      // Keyboard save updates actual emulator bytes, with CRLF and trailing spaces.
      await editor.press("Control+s");
      await expect(page.getByText("Saved to the printer")).toBeVisible();
      const expected = "# Edited in Playwright café  \r\n\r\n";
      expect(
        await (await page.request.get(`${emulator}/server/files/config/${filePath}`)).text(),
      ).toBe(expected);
      await page.reload();
      await expect(editor).toHaveValue(typed);

      // A second tab opens the same file directly and changes it.
      await editor.fill("# Local conflict draft\n\n");
      const outside = "# Other writer\r\n\r\n";
      const second = await page.context().newPage();
      try {
        await second.goto(page.url());
        const secondEditor = second.getByRole("textbox", { name: editorName, exact: true });
        await expect(secondEditor).toHaveValue(typed);
        await secondEditor.fill(outside.replaceAll("\r", ""));
        await secondEditor.press("Control+s");
        await expect(second.getByText("Saved to the printer")).toBeVisible();
      } finally {
        await second.close();
      }
      await page.getByRole("button", { name: "Save file", exact: true }).click();
      await expect(page.getByRole("alert")).toContainText("This file changed on the printer.");
      await expect(editor).toHaveValue("# Local conflict draft\n\n");
      await expect(page.getByRole("button", { name: "Save file", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "Review printer file" }).click();
      await expect(page.getByRole("heading", { name: "Current printer file" })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("config-conflict.png") });
      await page.getByRole("button", { name: "Use printer version" }).click();
      await expect(editor).toHaveValue(outside.replaceAll("\r", ""));

      const readOnlyPath = "includes/read-only.conf";
      const delayedRead = (url: URL) =>
        url.pathname.endsWith("/config/file") && url.searchParams.get("path") === readOnlyPath;
      const readGate = Promise.withResolvers<void>();
      await page.route(delayedRead, async (route) => {
        await readGate.promise;
        await route.continue();
      });
      try {
        await source.getByRole("button", { name: "read-only.conf", exact: true }).click();
        await expect(page.getByText(readOnlyPath, { exact: true })).toBeVisible();
        await expect(page.getByRole("textbox", { name: /Configuration text/ })).toHaveCount(0);
        await expect(page.getByRole("button", { name: "Save file", exact: true })).toHaveCount(0);
      } finally {
        readGate.resolve();
      }
      await expect(
        page.getByRole("textbox", {
          name: "includes/read-only.conf Configuration text",
          exact: true,
        }),
      ).toHaveAttribute("readonly", "");
      await page.unroute(delayedRead);
      await expect(page.getByRole("status").filter({ hasText: "Read-only" })).toBeVisible();
      await page.getByText("Reported configuration", { exact: true }).click();
      await expect(page.getByText("Klipper config", { exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("config-read-only.png") });
      await page.goBack();
      await expect(editor).toHaveValue(outside.replaceAll("\r", ""));

      const missing = new URL(page.url());
      missing.searchParams.set("file", "includes/removed.cfg");
      await page.goto(missing.href);
      await expect(
        page.getByText("This file is no longer available on the printer."),
      ).toBeVisible();
      await expect(page.getByText("includes/removed.cfg", { exact: true })).toBeVisible();
      await expect(page.getByRole("textbox", { name: /Configuration text/ })).toHaveCount(0);
      await expect(page.getByRole("button", { name: "Save file", exact: true })).toHaveCount(0);
      expect(await (await page.request.get(defaultSource)).text()).toBe(defaultContent);
    } finally {
      const current = await page.request.get(file);
      if (current.ok()) {
        const { revision }: { revision: string } = await current.json();
        await page.request.put(file, { data: { content: original, base_revision: revision } });
      }
      await page.request.delete(`${api}/printers/${id}`);
    }
  });
});
