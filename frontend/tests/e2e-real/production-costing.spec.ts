import { ADMIN, authBundleFor, expect, test } from "./helpers";
import {
  clickModelAction,
  collapseNavigationSidebar,
  gcodeFor,
  openModelPage,
  uploadModel,
} from "./util";

test.use({ actionTimeout: 15_000 });

test("saves an Artifact estimate with measured inputs and reusable rates", async ({ page }) => {
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
  await page.getByRole("button", { name: "New filament profile" }).click();
  const create = page.getByRole("form", { name: "Create filament preset" });
  await create.getByLabel("Name", { exact: true }).fill(`PLA ${stamp}`);
  const fromSpool = create.getByText("Calculate from spool price");
  await fromSpool.click();
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
    .getByRole("button", { name: "Add profile" })
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
    .getByRole("region", { name: `Profile: ${name}`, exact: true })
    .getByRole("button")
    .click();
  await page.getByRole("button", { name: `Edit profile ${name}` }).click();
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
  await form.getByRole("button", { name: "Save pricing template" }).click();
  await form.getByLabel("Pricing template", { exact: true }).selectOption({ label: name });
  await form.getByLabel("Profit margin (%)").fill("0");
  await form.getByLabel("Tax (%)").fill("0");
  await form.getByRole("button", { name: "Apply template" }).click();
  await expect(form.getByLabel("Profit margin (%)")).toHaveValue("20");
  await expect(form.getByLabel("Tax (%)")).toHaveValue("10");

  await form.getByRole("button", { name: "Save estimate" }).click();
  const cost = page.getByRole("region", { name: "Cost per piece" });
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
  expect((await page.request.delete(`${api}/printers/${printer.id}`, { headers })).ok()).toBe(true);
  expect(
    (await page.request.delete(`${api}/filament-profiles/${filament.id}`, { headers })).ok(),
  ).toBe(true);
  expect(
    (await page.request.delete(`${api}/printer-profiles/${preset.id}`, { headers })).ok(),
  ).toBe(true);
});
