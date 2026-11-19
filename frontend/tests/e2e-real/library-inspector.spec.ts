/** Inline revision outcomes persist through the real manual-print owner and a fresh Library read. */
import { test, expect } from "./helpers";
import { modelCard, uploadModel } from "./util";
import type { ModelPrintJobRead, ModelRead } from "../../src/types";

test.describe("Library inline inspector", () => {
  test("persists a failed revision outcome from inline inspection", async ({ page }) => {
    const stamp = Date.now();
    const name = `e2e-inspector-${stamp}`;
    const printerName = `Inspector rig ${stamp}`;
    const note = `Corner lifted ${stamp}`;
    const created = await page.request.post("/api/v1/printers", {
      data: {
        name: printerName,
        provider: "moonraker",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`,
      },
    });
    expect(created.ok()).toBe(true);
    const printer: { id: number } = await created.json();
    let modelId: number | null = null;
    try {
      await uploadModel(page, name, { gcode: true });
      const card = modelCard(page, name);
      const href = await card.getAttribute("href");
      const match = href?.match(/^\/models\/(\d+)/);
      if (!match) throw new Error("Uploaded Model card has no model route");
      modelId = Number(match[1]);
      const detail = await page.request.get(`/api/v1/models/${modelId}`);
      expect(detail.ok()).toBe(true);
      const model: ModelRead = await detail.json();
      const file = model.files.find((item) => item.file_type === "gcode");
      if (!file) throw new Error("Uploaded Model has no G-code revision");

      await card.click();
      const drawer = page.locator("#model-drawer");
      await expect(drawer).toBeVisible();
      expect(new URL(page.url()).pathname).toBe("/");
      await drawer
        .getByRole("combobox", { name: "Printer for printing and logging" })
        .selectOption(String(printer.id));
      await expect(drawer.getByRole("button", { name: "Failed", exact: true })).toBeEnabled();
      await drawer.getByRole("tab", { name: "Print", exact: true }).focus();
      await page.keyboard.press("2");
      const failureNote = drawer.getByRole("textbox", { name: /What went wrong/ });
      await expect(failureNote).toBeFocused();
      await failureNote.fill(note);
      const accepted = page.waitForResponse(
        (response) =>
          response.request().method() === "POST" &&
          new URL(response.url()).pathname === `/api/v1/models/${modelId}/print-jobs`,
      );
      await failureNote.press("Control+Enter");
      const response = await accepted;
      expect(response.ok()).toBe(true);
      const receipt: ModelPrintJobRead = await response.json();
      expect(receipt).toMatchObject({
        file_id: file.id,
        printer_id: printer.id,
        printer_name: printerName,
        state: "failed",
        source: "manual",
        error: note,
      });
      expect(receipt.id).toBeGreaterThan(0);
      const historyResponse = await page.request.get(`/api/v1/models/${modelId}/print-jobs`);
      expect(historyResponse.ok()).toBe(true);
      const persisted: ModelPrintJobRead[] = await historyResponse.json();
      expect(persisted).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            id: receipt.id,
            file_id: file.id,
            printer_id: printer.id,
            state: "failed",
            error: note,
          }),
        ]),
      );

      await page.reload();
      await modelCard(page, name).click();
      await drawer.getByRole("tab", { name: /History/ }).click();
      const historyPanel = drawer.getByRole("tabpanel", { name: /History/ });
      await expect(historyPanel.getByText(note, { exact: true })).toBeVisible();
      await expect(historyPanel.getByText(new RegExp(printerName))).toBeVisible();
    } finally {
      if (modelId !== null) {
        expect((await page.request.delete(`/api/v1/models/${modelId}`)).ok()).toBe(true);
        expect((await page.request.delete(`/api/v1/models/${modelId}/purge`)).ok()).toBe(true);
      }
      expect((await page.request.delete(`/api/v1/printers/${printer.id}`)).ok()).toBe(true);
    }
  });
});

test("reveals a tree model beyond the first listing page", async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  const response = await page.request.post("/api/v1/config/sample-collection");
  expect(response.status()).toBe(202);
  await expect
    .poll(async () => {
      const browse = await page.request.get("/api/v1/models/browse?collection=printstash-samples");
      return (await browse.json()).total;
    })
    .toBe(3);
  await page.goto("/?sort=name-asc");
  const sidebar = page.getByRole("complementary", { name: "Collections", exact: true });
  const expand = sidebar.getByRole("button", { name: "Expand PrintStash samples", exact: true });
  await expand.click();
  let continuations = 0;
  await page.route("**/api/v1/models/browse?**", async (route) => {
    const url = new URL(route.request().url());
    if (url.searchParams.get("collection") === "printstash-samples") {
      if (url.searchParams.has("cursor")) continuations += 1;
      url.searchParams.set("limit", "1");
    }
    await route.continue({ url: url.toString() });
  });
  const leaf = sidebar.getByRole("treeitem", { name: "Small parts tray", exact: true });
  await leaf.click();
  await expect(page).toHaveURL(/c=printstash-samples/);
  const drawer = page.getByRole("complementary", { name: "Model details" });
  await expect(
    drawer.getByRole("heading", { name: "Small parts tray", exact: true }),
  ).toBeVisible();
  await expect(modelCard(page, "Small parts tray")).toBeInViewport();
  expect(continuations).toBeGreaterThan(0);
  await leaf.dblclick();
  await expect(page).toHaveURL(/\/models\/\d+/);
});
