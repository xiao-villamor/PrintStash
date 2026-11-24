/** Verifies saved per-piece costs using real profiles, print history and pricing templates. */
import { ADMIN, authBundleFor, expect, test } from "./helpers";
import {
  clickModelAction,
  collapseNavigationSidebar,
  gcodeFor,
  openModelPage,
  uploadModel,
} from "./util";

test.use({ actionTimeout: 15_000 });

test.describe("production costing", () => {
  test("persists a per-piece estimate through reload", async ({ page }) => {
    const stamp = Date.now();
    const name = `costing-${stamp}`;
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;
    const moonraker = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`;
    const { token } = await authBundleFor(ADMIN.username, ADMIN.password);
    const headers = { Authorization: `Bearer ${token}` };

    // Configure material cost from the purchase receipt.
    await page.goto("/");
    await collapseNavigationSidebar(page);
    await page.goto("/profiles");
    await page.getByRole("button", { name: "New filament preset" }).click();
    const create = page.getByRole("form", { name: "Create filament preset" });
    await create.getByLabel("Name", { exact: true }).fill(`PLA ${stamp}`);
    await create.getByLabel(/Spool price/).fill("10");
    await create.getByLabel("Net filament weight (g)").fill("0");
    await expect(create.getByRole("button", { name: "Use calculated cost per kg" })).toBeDisabled();
    await create.getByLabel("Net filament weight (g)").fill("500");
    await create.getByRole("button", { name: "Use calculated cost per kg" }).click();
    await expect(create.getByLabel(/Cost per kg/)).toHaveValue("20");
    const filamentWrite = page.waitForResponse(
      (response) =>
        response.url().endsWith("/filament-profiles") && response.request().method() === "POST",
    );
    await page
      .getByRole("dialog", { name: "New filament preset" })
      .getByRole("button", { name: "Add preset" })
      .click();
    const filamentReceipt = await filamentWrite;
    expect(filamentReceipt.status()).toBe(201);
    const filament: { id: number } = await filamentReceipt.json();

    const printerPresetResponse = await page.request.post(`${api}/printer-profiles`, {
      headers,
      data: { name },
    });
    expect(printerPresetResponse.status()).toBe(201);
    const preset: { id: number } = await printerPresetResponse.json();
    await page.reload();
    await page.getByRole("tab", { name: /Printers/ }).click();
    await page
      .getByRole("region", { name: `Preset: ${name}`, exact: true })
      .getByRole("button")
      .click();
    await page.getByRole("button", { name: `Edit preset ${name}` }).click();
    const editor = page.getByRole("dialog", { name: "Edit printer preset" });
    await editor.getByLabel(/Purchase cost/).fill("500");
    await editor.getByLabel("Expected lifetime (print hours)").fill("1000");
    await editor.getByLabel(/Maintenance over lifetime/).fill("100");
    await editor.getByLabel("Average power (W)").fill("200");
    await editor.getByLabel(/Electricity per kWh/).fill("0.25");
    await editor.getByLabel(/Additional cost per print hour/).fill("1");
    const expenseWrite = page.waitForResponse(
      (response) =>
        response.url().endsWith(`/printer-profiles/${preset.id}`) &&
        response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "Close", exact: true }).click();
    const expenseReceipt = await expenseWrite;
    expect(expenseReceipt.ok()).toBe(true);
    expect((await expenseReceipt.json()).expenses).toMatchObject({
      acquisition_cost: 500,
      lifetime_hours: 1000,
      lifetime_maintenance: 100,
      power_watts: 200,
      electricity_per_kwh: 0.25,
      extra_per_hour: 1,
    });

    // Produce and import a real Moonraker history record from the emulator.
    await uploadModel(page, name, { gcode: true });
    await openModelPage(page, name);
    const modelPath = new URL(page.url()).pathname;
    const filename = `${name}.gcode`;
    const started = await page.request.post(`${moonraker}/server/files/upload`, {
      multipart: {
        print: "true",
        file: { name: filename, mimeType: "text/plain", buffer: Buffer.from(gcodeFor(name)) },
      },
    });
    expect(started.ok()).toBe(true);
    await expect
      .poll(async () => {
        await page.request.get(`${moonraker}/printer/objects/query?print_stats`);
        const history = await page.request.get(`${moonraker}/server/history/list`);
        const payload: { result: { jobs: { filename: string }[] } } = await history.json();
        return payload.result.jobs.some((job) => job.filename === filename);
      })
      .toBe(true);
    const printerResponse = await page.request.post(`${api}/printers`, {
      headers,
      data: { name, moonraker_url: moonraker, provider: "moonraker" },
    });
    expect(printerResponse.status()).toBe(201);
    const printer: { id: number } = await printerResponse.json();
    const imported = await page.request.post(
      `${api}${modelPath}/print-jobs/import-printer/${printer.id}`,
      { headers },
    );
    expect(imported.ok()).toBe(true);
    expect(await imported.json()).toEqual(
      expect.arrayContaining([expect.objectContaining({ imported: true })]),
    );

    await page.goto(`${modelPath}?tab=costing`);
    await page.getByRole("button", { name: "Create estimate" }).click();
    const form = page.getByRole("form", { name: "Production estimate" });
    const saveEstimate = form.getByRole("button", { name: "Save estimate" });
    await expect(saveEstimate).toBeInViewport();
    const time = form.getByLabel("Print time (minutes)");
    const grams = form.getByLabel("Filament used (g)");
    await time.fill("20");
    await grams.fill("1");
    await form.getByRole("button", { name: /Use latest print measurements/ }).click();
    await expect(time).toHaveValue("10");
    await expect(grams).toHaveValue("3.58");
    await form
      .getByLabel("Fill from filament preset or spool")
      .selectOption({ label: `PLA ${stamp}` });
    await expect(form.getByLabel(/Cost per kg/)).toHaveValue("20");
    await form.getByLabel("Printer operating costs").selectOption({ label: name });
    await form.getByLabel("Pieces per print").fill("2");
    await time.fill("120");
    await form.getByLabel("Filament used (g)").fill("100");

    // Save reusable pricing rates and apply them to the pending estimate.
    await form.getByLabel("Profit margin (%)").fill("20");
    await form.getByLabel("Tax (%)").fill("10");
    await form.getByLabel("New template name").fill(name);
    await expect(saveEstimate).toBeInViewport();
    await form.getByRole("button", { name: "Save pricing template" }).click();
    await form.getByLabel("Pricing template", { exact: true }).selectOption({ label: name });
    await form.getByLabel("Profit margin (%)").fill("0");
    await form.getByLabel("Tax (%)").fill("0");
    await form.getByRole("button", { name: "Apply template" }).click();
    await expect(form.getByLabel("Profit margin (%)")).toHaveValue("20");
    await expect(form.getByLabel("Tax (%)")).toHaveValue("10");

    await form.getByRole("button", { name: "Save estimate" }).click();
    const cost = page.getByRole("region", { name: "Cost per piece" });
    await expect(cost.getByRole("heading", { name: "Production cost breakdown" })).toBeVisible();
    await expect(cost.getByText("3.6438", { exact: true })).toBeVisible();
    await page.reload();
    await expect(cost.getByText("3.6438", { exact: true })).toBeVisible();
    await expect(cost.getByText("2.65", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "Edit estimate" }).click();
    await expect(form.getByLabel("Filament used (g)")).toHaveValue("100");
    await form.getByLabel("Pricing template", { exact: true }).selectOption({ label: name });
    await form.getByRole("button", { name: "Delete template" }).click();
    await page
      .getByRole("dialog", { name: "Delete pricing template?" })
      .getByRole("button", { name: "Delete template" })
      .click();
    await expect(
      form.getByLabel("Pricing template", { exact: true }).getByRole("option", { name }),
    ).toHaveCount(0);
    await expect(cost.getByText("3.6438", { exact: true })).toBeVisible();

    await clickModelAction(page, "Delete model");
    await page.getByRole("dialog").getByRole("button", { name: "Delete", exact: true }).click();
    expect((await page.request.delete(`${api}/printers/${printer.id}`, { headers })).ok()).toBe(
      true,
    );
    expect(
      (await page.request.delete(`${api}/filament-profiles/${filament.id}`, { headers })).ok(),
    ).toBe(true);
    expect(
      (await page.request.delete(`${api}/printer-profiles/${preset.id}`, { headers })).ok(),
    ).toBe(true);
  });
  test("records a stopped print using the assigned cost profile", async ({ page }, testInfo) => {
    const name = `connected-cost-${Date.now()}`;
    const moonraker = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`;
    const profileResponse = await page.request.post("/api/v1/printer-profiles", {
      data: {
        name,
        expenses: {
          acquisition_cost: 500,
          lifetime_hours: 1000,
          lifetime_maintenance: 100,
          power_watts: 200,
          electricity_per_kwh: 0.25,
          extra_per_hour: 1,
        },
      },
    });
    expect(profileResponse.ok()).toBe(true);
    const profile: { id: number } = await profileResponse.json();
    const printerResponse = await page.request.post("/api/v1/printers", {
      data: { name, moonraker_url: moonraker, provider: "moonraker" },
    });
    expect(printerResponse.ok()).toBe(true);
    const printer: { id: number } = await printerResponse.json();
    const printerPath = `/printers/${printer.id}`;
    const api = `/api/v1${printerPath}`;
    let modelPath: string | undefined;
    try {
      await page.goto(`${printerPath}?tab=settings`);
      await collapseNavigationSidebar(page);
      const picker = page.getByRole("combobox", { name: "Cost profile" });
      await picker.selectOption(String(profile.id));
      await page.getByRole("button", { name: "Save changes", exact: true }).click();
      await expect(page.getByText("Saved", { exact: true })).toBeVisible();
      await page.reload();
      await expect(picker).toBeEnabled();
      await expect(picker).toHaveValue(String(profile.id));
      await page.screenshot({ path: testInfo.outputPath("assigned-cost-profile.png") });

      await uploadModel(page, name);
      await openModelPage(page, name);
      modelPath = new URL(page.url()).pathname;
      const printerStatus = page.locator(".detail-printer-status");
      await expect(printerStatus.getByText(/\d+ ready/)).toBeVisible();
      await printerStatus.screenshot({ path: testInfo.outputPath("printer-status-summary.png") });
      const detail = await page.request.get(`/api/v1${modelPath}`);
      const { files } = await detail.json();
      const started = await page.request.post(`${api}/send`, {
        data: { file_id: files[0].id, start_print: true },
      });
      expect(started.ok(), await started.text()).toBe(true);
      const job = await started.json();
      expect(job.production_cost).toMatchObject({ estimated: true, machine: 0.8305 });
      expect((await page.request.post(`${moonraker}/printer/print/pause`)).ok()).toBe(true);
      expect((await page.request.post(`${api}/cancel`)).ok()).toBe(true);
      await expect
        .poll(async () => {
          const history = await (await page.request.get(`${api}/jobs`)).json();
          return history.find((row: { id: number }) => row.id === job.id)?.state;
        })
        .toBe("cancelled");

      await page.goto(`${printerPath}?tab=jobs`);
      const row = page.getByRole("row").filter({ hasText: `Run #${job.id}` });
      await expect(row).toContainText("Cancelled");
      await expect(row).toContainText(/partial cost/i);
      await page.screenshot({ path: testInfo.outputPath("stopped-print-cost.png") });
    } finally {
      if (modelPath) await page.request.delete(`/api/v1${modelPath}`);
      await page.request.delete(api);
      await page.request.delete(`/api/v1/printer-profiles/${profile.id}`);
    }
  });
  test("assigns an existing profile with missing operating costs", async ({ page }, testInfo) => {
    const name = `Workshop P1S ${Date.now()}`;
    const createProfile = await page.request.post("/api/v1/printer-profiles", {
      data: { name, printer_model: "Bambu Lab P1S", nozzle_diameter_mm: 0.4 },
    });
    expect(createProfile.ok()).toBe(true);
    const profile: { id: number } = await createProfile.json();
    const createPrinter = await page.request.post("/api/v1/printers", {
      data: {
        name,
        model_name: "Bambu Lab P1S",
        moonraker_url: `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`,
      },
    });
    expect(createPrinter.ok()).toBe(true);
    const printer: { id: number } = await createPrinter.json();
    let opened: typeof page | undefined;

    try {
      await page.emulateMedia({ colorScheme: "dark" });
      await page.goto("/printers");
      await collapseNavigationSidebar(page);
      const card = page.getByRole("link", { name: new RegExp(name) });
      await page.getByRole("tab", { name: "Manage printers", exact: true }).click();
      const settings = page
        .getByRole("row")
        .filter({ has: card })
        .getByRole("button", { name: "Settings", exact: true });
      await settings.click();
      const dialog = page.getByRole("dialog", { name: "Edit printer connection" });
      const picker = dialog.getByRole("combobox", { name: "Cost profile" });
      await picker.selectOption(String(profile.id));
      await expect(
        dialog.getByText("Add operating costs to this profile to start tracking machine costs."),
      ).toBeVisible();
      await dialog.screenshot({ path: testInfo.outputPath("printer-settings-before-costs.png") });
      await dialog.getByRole("button", { name: "Save changes" }).click();
      await expect(dialog).toBeHidden();
      const receipt = await page.request.get(`/api/v1/printers/${printer.id}`);
      expect((await receipt.json()).cost_profile_id).toBe(profile.id);

      await settings.click();
      await expect(picker).toHaveValue(String(profile.id));
      const catalogTab = page.waitForEvent("popup");
      await dialog.getByRole("link", { name: "Manage profiles" }).click();
      opened = await catalogTab;
      await opened.emulateMedia({ colorScheme: "dark" });
      await collapseNavigationSidebar(opened);
      const details = opened
        .getByRole("complementary")
        .filter({ has: opened.getByRole("heading", { name }) });
      await expect(details.getByText("Cost not configured")).toBeVisible();
      await expect(
        details.getByRole("region", { name: "Assigned printers" }).getByRole("link", { name }),
      ).toHaveAttribute("href", `/printers/${printer.id}?tab=settings`);
      await details.getByRole("button", { name: "Set operating costs" }).click();
      const editor = opened.getByRole("dialog", { name: "Edit printer preset" });
      const purchase = editor.getByLabel(/Purchase cost/);
      await expect(purchase).toBeFocused();
      await purchase.fill("500");
      await editor.getByLabel("Expected lifetime (print hours)").fill("1000");
      await editor.getByRole("button", { name: "Close", exact: true }).click();
      await expect(details.getByRole("status", { name: "Machine cost per print hour" })).toHaveText(
        "$0.50",
      );
      await opened.reload();
      await expect(details.getByRole("status", { name: "Machine cost per print hour" })).toHaveText(
        "$0.50",
      );
      await opened.screenshot({ path: testInfo.outputPath("profiles-after.png") });
      await opened.close();
      opened = undefined;

      await page.bringToFront();
      await expect(
        dialog.getByText("This profile supplies machine costs for new prints."),
      ).toBeVisible();
      await dialog.screenshot({ path: testInfo.outputPath("printer-settings-after.png") });
    } finally {
      await opened?.close();
      await page.request.delete(`/api/v1/printers/${printer.id}`);
      await page.request.delete(`/api/v1/printer-profiles/${profile.id}`);
    }
  });
});
