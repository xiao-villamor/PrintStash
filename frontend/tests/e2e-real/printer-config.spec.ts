/** Saving a configuration file must target the selected source, preserve its bytes and surface concurrent changes. */
import { expect, test } from "./helpers";
import { collapseNavigationSidebar } from "./util";

const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;
const emulator = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`;
const filePath = "includes/disposable.cfg";
const original = "# Disposable emulator configuration\r\n\r\n";

test.describe("Moonraker configuration files", () => {
  test("keeps source lines aligned with their numbers while paging through a long file", async ({
    page,
  }, testInfo) => {
    const created = await page.request.post(`${api}/printers`, {
      data: { name: `Config geometry ${Date.now()}`, moonraker_url: emulator },
    });
    expect(created.ok()).toBe(true);
    const { id }: { id: number } = await created.json();
    const file = `${api}/printers/${id}/config/file?${new URLSearchParams({ path: filePath })}`;
    const lines = Array.from({ length: 80 }, (_, index) => `# Source line ${index + 1}`);
    lines[1] += " ".repeat(400) + "end";
    const content = lines.join("\r\n") + "\r\n";

    async function replaceDisposableFile(contents: string) {
      const current = await page.request.get(file);
      expect(current.ok()).toBe(true);
      const { revision }: { revision: string } = await current.json();
      expect(
        (
          await page.request.put(file, { data: { content: contents, base_revision: revision } })
        ).ok(),
      ).toBe(true);
    }

    try {
      await replaceDisposableFile(content);
      await page.goto(`/printers/${id}?tab=config&${new URLSearchParams({ file: filePath })}`);
      await collapseNavigationSidebar(page);
      const source = page.getByRole("navigation", { name: "Configuration file browser" });
      const editor = page.getByRole("textbox", { name: `${filePath} Configuration text` });
      await expect(editor).toHaveValue(content.replaceAll("\r", ""));
      await page.addStyleTag({
        content: "textarea::-webkit-scrollbar { width: 16px; height: 16px; }",
      });

      const geometry = () =>
        editor.evaluate((textarea: HTMLTextAreaElement) => {
          const frame = textarea.parentElement!;
          const numbers = frame.querySelector("pre")!;
          const sourceStyle = getComputedStyle(textarea);
          const numberStyle = getComputedStyle(numbers);
          const sourceBox = textarea.getBoundingClientRect();
          const numberBox = numbers.getBoundingClientRect();
          const gutterBox = numbers.parentElement!.getBoundingClientRect();
          const sourceTop = sourceBox.top + parseFloat(sourceStyle.paddingTop) - textarea.scrollTop;
          const numberTop = numberBox.top + parseFloat(numberStyle.paddingTop) - numbers.scrollTop;
          return {
            frameHeight: frame.clientHeight,
            gutterHeight: gutterBox.height,
            editorHeight: textarea.offsetHeight,
            lineHeight: parseFloat(sourceStyle.lineHeight),
            gutterLineHeight: parseFloat(numberStyle.lineHeight),
            lineOffset: Math.abs(sourceTop - numberTop),
            scrollTop: textarea.scrollTop,
            scrollLeft: textarea.scrollLeft,
            scrollEnd: textarea.scrollHeight - textarea.clientHeight,
            lineNumbers: numbers.textContent?.split("\n").length,
            lastNumber: numbers.textContent?.split("\n").at(-1),
            lastLineFits:
              numberTop + parseFloat(numberStyle.lineHeight) * textarea.value.split("\n").length <=
              sourceBox.top + textarea.clientHeight + 1,
            wideLineScrolls: textarea.scrollWidth > textarea.clientWidth,
          };
        });

      const initial = await geometry();
      await page.screenshot({ path: testInfo.outputPath("config-geometry-top.png") });
      expect(initial.editorHeight).toBe(initial.frameHeight);
      expect(initial.frameHeight).toBeLessThan(600);
      const sidebar = (await source.boundingBox())!;
      const boundsAtTop = (await editor.boundingBox())!;
      expect(sidebar.x + sidebar.width).toBeLessThan(boundsAtTop.x);
      expect(sidebar.height).toBeLessThan(650);
      expect(initial.gutterHeight).toBeCloseTo(initial.editorHeight, 0);
      expect(initial.editorHeight / initial.lineHeight).toBeGreaterThan(10);
      expect(initial.lineHeight).toBe(initial.gutterLineHeight);
      expect(initial.lineNumbers).toBe(81);
      expect(initial.lastNumber).toBe("81");
      expect(initial.wideLineScrolls).toBe(true);
      expect(initial.scrollTop).toBe(0);
      expect(initial.lineOffset).toBeLessThan(1);

      await editor.press("ArrowDown");
      await editor.press("End");
      await expect.poll(async () => (await geometry()).scrollLeft).toBeGreaterThan(0);
      await expect.poll(async () => (await geometry()).lineOffset).toBeLessThan(1);
      await editor.press("Control+Home");

      await editor.press("PageDown");
      await expect.poll(async () => (await geometry()).scrollTop).toBeGreaterThan(0);
      await expect.poll(async () => (await geometry()).lineOffset).toBeLessThan(1);
      await editor.press("Control+End");
      await expect.poll(async () => (await geometry()).lineOffset).toBeLessThan(1);
      const bounds = (await editor.boundingBox())!;
      await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      await page.mouse.wheel(0, 500);
      await expect
        .poll(async () => {
          const { scrollEnd, scrollTop, lineOffset } = await geometry();
          return Math.max(Math.abs(scrollEnd - scrollTop), lineOffset);
        })
        .toBeLessThan(1);
      expect((await geometry()).lastLineFits).toBe(true);
      await page.screenshot({ path: testInfo.outputPath("config-geometry-end.png") });

      await source.getByRole("button", { name: "read-only.conf", exact: true }).click();
      const shortEditor = page.getByRole("textbox", { name: /read-only.conf Configuration text/ });
      await expect(shortEditor).toBeVisible();
      expect((await shortEditor.boundingBox())!.height).toBeLessThan(initial.editorHeight);
      await source.getByRole("button", { name: "disposable.cfg", exact: true }).click();
      await expect(editor).toHaveValue(content.replaceAll("\r", ""));
      await expect.poll(async () => (await geometry()).scrollTop).toBe(0);
      await expect.poll(async () => (await geometry()).lineOffset).toBeLessThan(1);
    } finally {
      try {
        await replaceDisposableFile(original);
      } finally {
        await page.request.delete(`${api}/printers/${id}`);
      }
    }
  });

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

      const reportedTab = page.getByRole("tab", { name: "Reported configuration" });
      const filesTab = page.getByRole("tab", { name: "Configuration files" });
      await filesTab.focus();
      await filesTab.press("ArrowRight");
      await expect(reportedTab).toHaveAttribute("aria-selected", "true");
      const reported = page.getByRole("tabpanel", { name: "Reported configuration" });
      await expect(reported.getByText("No sections reported.")).toBeVisible();
      await reported.getByRole("tab", { name: "Server info" }).click();
      await expect(reported.getByRole("region", { name: "Reported values" })).toContainText(
        "klippy_state",
      );
      await expect(
        reported.getByRole("searchbox", { name: "Search configuration sections" }),
      ).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("config-reported.png") });
      await filesTab.click();
      await expect(editor).toHaveValue(typed);
      await expect(page.getByText("Unsaved edits", { exact: true })).toBeVisible();
      await expect(page).toHaveURL((url) => url.searchParams.get("file") === filePath);

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
      await page.getByRole("tab", { name: "Reported configuration" }).click();
      await expect(page.getByRole("tab", { name: "Klipper config", exact: true })).toBeVisible();
      await page.screenshot({ path: testInfo.outputPath("config-read-only.png") });
      await filesTab.click();
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
