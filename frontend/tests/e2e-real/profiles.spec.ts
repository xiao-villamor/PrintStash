/**
 * Filament and printer presets — the values every cost estimate is computed from.
 *
 * A preset is edited once and read by every print afterwards, so an edit that appears to
 * save and does not is a whole library of wrong numbers. Both flows here therefore create,
 * edit, and delete rather than stopping at the first green tick.
 */
import { type Locator } from "@playwright/test";
import { test, expect, authBundleFor, authedContext, ADMIN } from "./helpers";
import { DESKTOP_CHROMIUM } from "../playwright-desktop";
import { collapseNavigationSidebar } from "./util";

// Resolve the unique name to the server-ID label in one DOM read. Retaining
// an ordinal locator would follow a different row if the refreshed list sorts.
async function inputByValue(scope: Locator, labelRe: RegExp, value: string): Promise<Locator> {
  const page = scope.page();
  const existing = page.getByRole("dialog");
  if (await existing.count())
    await existing.getByRole("button", { name: "Close", exact: true }).click();
  const row = scope.getByRole("region", { name: `Preset: ${value}`, exact: true });
  await row.getByRole("button").click();
  await page.getByRole("button", { name: `Edit preset ${value}`, exact: true }).click();
  const inputs = page.getByLabel(labelRe);
  let label: string | null = null;
  await expect
    .poll(async () => {
      label = await inputs.evaluateAll((fields, expectedValue) => {
        const field = fields.find(
          (input) => input instanceof HTMLInputElement && input.value === expectedValue,
        );
        return field?.getAttribute("aria-label") ?? null;
      }, value);
      return label;
    })
    .not.toBeNull();
  if (!label) throw new Error(`no input with value ${value}`);
  const input = page.getByLabel(label, { exact: true });
  await expect(input).toHaveValue(value);
  return input;
}

function rowOf(input: Locator): Locator {
  return input.locator("xpath=ancestor::div[@role='dialog'][1]");
}

test.describe("profiles", () => {
  test("links both preset types to the connected printer", async ({ page }) => {
    const name = `e2e-profile-links-${Date.now()}`;
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;
    const moonraker = `http://127.0.0.1:${process.env.PLAYWRIGHT_MOCK_PRINTER_PORT ?? 7530}`;
    const { token } = await authBundleFor(ADMIN.username, ADMIN.password);
    const headers = { Authorization: `Bearer ${token}` };
    const profile = await page.request.post(`${api}/printer-profiles`, { headers, data: { name } });
    expect(profile.ok()).toBe(true);
    const { id: profileId } = await profile.json();
    const filament = await page.request.post(`${api}/filament-profiles`, {
      headers,
      data: { name, material_type: "PLA", material_brand: name },
    });
    expect(filament.ok()).toBe(true);
    const { id: filamentId } = await filament.json();
    let printerId: number | undefined;
    try {
      const connected = await page.request.post(`${api}/printers`, {
        headers,
        data: { name, provider: "moonraker", moonraker_url: moonraker, cost_profile_id: profileId },
      });
      expect(connected.ok()).toBe(true);
      printerId = (await connected.json()).id;
      const material = await page.request.put(
        `${api}/printers/${printerId}/material-state/manual`,
        {
          headers,
          data: {
            slots: [
              {
                slot_key: "manual0",
                label: "Feed 1",
                state: "loaded",
                material_type: "PLA",
                material_brand: name,
              },
            ],
          },
        },
      );
      expect(material.ok()).toBe(true);

      await page.goto(`/profiles?tab=printers&profile=${profileId}`);
      await collapseNavigationSidebar(page);
      const assigned = page.getByRole("region", { name: "Assigned printers" });
      await assigned.getByRole("link", { name, exact: true }).click();
      await expect(page).toHaveURL(new RegExp(`/printers/${printerId}\\?tab=settings`));

      await page.goto("/profiles?tab=filaments");
      await page
        .getByRole("region", { name: `Preset: ${name}` })
        .getByRole("button")
        .click();
      const matching = page.getByRole("region", { name: "Printers with matching filament" });
      await expect(matching.getByText("Feed 1")).toBeVisible();
      await matching.getByRole("link", { name: new RegExp(name) }).click();
      await expect(page).toHaveURL(new RegExp(`/printers/${printerId}\\?tab=materials`));
    } finally {
      if (printerId) await page.request.delete(`${api}/printers/${printerId}`, { headers });
      await page.request.delete(`${api}/printer-profiles/${profileId}`, { headers });
      await page.request.delete(`${api}/filament-profiles/${filamentId}`, { headers });
    }
  });

  for (const kind of ["filament", "printer"] as const) {
    test(`discards a canceled new ${kind} preset`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.goto("/profiles");
      if (kind === "printer") await page.getByRole("tab", { name: /Printers/ }).click();
      const writes: string[] = [];
      page.on("request", (request) => {
        if (request.method() === "POST" && request.url().endsWith(`/api/v1/${kind}-profiles`)) {
          writes.push(request.url());
        }
      });
      const opener = page.getByRole("button", { name: `New ${kind} preset`, exact: true });
      await opener.click();
      const dialog = page.getByRole("dialog", { name: `New ${kind} preset` });
      const form = dialog.getByRole("form", { name: `Create ${kind} preset` });
      const cancel = dialog.getByRole("button", { name: "Cancel", exact: true });
      const submit = dialog.getByRole("button", { name: "Add preset", exact: true });
      await expect(form).toBeVisible();
      for (const field of await form.getByRole("textbox").all()) await field.fill("12");

      await cancel.click();

      await expect(form).toHaveCount(0);
      await expect(opener).toBeFocused();
      await opener.press("Enter");
      await expect(form).toBeVisible();
      for (const field of await form.getByRole("textbox").all())
        await expect(field).toHaveValue("");
      await expect(submit).toBeDisabled();
      expect(writes).toEqual([]);
      await cancel.click();
    });
  }

  test("create, edit, and delete a filament preset", async ({ page }) => {
    const name = `e2e-pla-${Date.now()}`;
    await page.goto("/profiles");
    await collapseNavigationSidebar(page);

    const section = page.locator("section", { hasText: "Filament presets" });
    await page.getByRole("button", { name: /New filament/ }).click();
    const createForm = page.getByRole("form", { name: "Create filament preset" });
    await createForm.getByLabel("Name").fill(name);
    await createForm.getByLabel("Material").fill("PLA");
    await createForm.getByLabel("Cost per kg").fill("25");
    await page
      .getByRole("dialog", { name: "New filament preset" })
      .getByRole("button", { name: "Add preset" })
      .click();

    await section
      .getByRole("region", { name: `Preset: ${name}`, exact: true })
      .getByRole("button")
      .click();
    const profileHeading = page.getByRole("heading", { name, exact: true });
    await expect(
      profileHeading.locator("xpath=ancestor::header").locator("svg").first(),
    ).toHaveClass(/\blucide-filament-spool\b/);
    const nameInput = await inputByValue(section, /^Filament preset name/, name);
    await expect(nameInput).toBeVisible();

    // Closing the editor saves the new cost.
    await rowOf(nameInput)
      .getByLabel(/Filament cost per kg/)
      .fill("42");
    await Promise.all([
      page.waitForResponse(
        (r) =>
          /\/api\/v1\/filament-profiles\/\d+/.test(r.url()) && r.request().method() === "PATCH",
      ),
      page
        .getByRole("dialog", { name: "Edit filament preset" })
        .getByRole("button", { name: "Close", exact: true })
        .click(),
    ]);

    // Cost persisted.
    await page.reload();
    const reloaded = await inputByValue(section, /^Filament preset name/, name);
    await expect(rowOf(reloaded).getByLabel(/Filament cost per kg/)).toHaveValue("42");

    // Delete it.
    await rowOf(reloaded)
      .getByRole("button", { name: `Delete filament preset ${name}` })
      .click();
    await page
      .getByRole("dialog", { name: "Delete filament preset?" })
      .getByRole("button", { name: "Delete preset" })
      .click();
    // Counting inputs before reading each one races with the row disappearing.
    await expect(section.getByRole("region", { name: `Preset: ${name}`, exact: true })).toHaveCount(
      0,
    );
  });

  test("keeps an invalid local draft while another preset refreshes the catalog", async ({
    page,
  }) => {
    const name = `e2e-draft-${Date.now()}`;
    const second = `${name}-second`;
    await page.goto("/profiles");
    const section = page.locator("section", { hasText: "Filament presets" });
    const add = async (value: string) => {
      const editor = page.getByRole("dialog");
      if (await editor.count())
        await editor.getByRole("button", { name: "Close", exact: true }).click();
      await page.getByRole("button", { name: /New filament/ }).click();
      const create = page.getByRole("dialog", { name: "New filament preset" });
      const form = create.getByRole("form", { name: "Create filament preset" });
      await form.getByLabel("Name").fill(value);
      await form.getByLabel("Cost per kg").fill("25");
      await create.getByRole("button", { name: "Add preset" }).click();
      return inputByValue(section, /^Filament preset name/, value);
    };
    const first = await add(name);
    const cost = rowOf(first).getByLabel(/Filament cost per kg/);
    await cost.fill("-1");

    await add(second);
    await inputByValue(section, /^Filament preset name/, name);

    await expect(cost).toHaveValue("-1");
    await expect(cost).toHaveAttribute("aria-invalid", "true");
    await expect(page.getByRole("dialog").getByText("Cost must be 0 or more.")).toBeVisible();
    await cost.fill("42");
    const edit = page.getByRole("dialog", { name: "Edit filament preset" });
    await Promise.all([
      page.waitForResponse(
        (response) =>
          /\/api\/v1\/filament-profiles\/\d+/.test(response.url()) &&
          response.request().method() === "PATCH",
      ),
      edit.getByRole("button", { name: "Close", exact: true }).click(),
    ]);
    await expect(edit).toHaveCount(0);
    await page.reload();
    const persisted = await inputByValue(section, /^Filament preset name/, name);
    await expect(rowOf(persisted).getByLabel(/Filament cost per kg/)).toHaveValue("42");
    for (const value of [name, second]) {
      const row = await inputByValue(section, /^Filament preset name/, value);
      await rowOf(row)
        .getByRole("button", { name: `Delete filament preset ${value}` })
        .click();
      await page
        .getByRole("dialog", { name: "Delete filament preset?" })
        .getByRole("button", { name: "Delete preset" })
        .click();
      await expect(page.getByRole("dialog", { name: "Delete filament preset?" })).toBeHidden();
    }
  });

  test("persists printer hardware choices and operating cost overrides", async ({ page }) => {
    const name = `e2e-printer-${Date.now()}`;
    await page.goto("/profiles");
    await collapseNavigationSidebar(page);

    await page.getByRole("tab", { name: /Printers/ }).click();
    const section = page.locator("section", { hasText: "Printer presets" });
    await page.getByRole("button", { name: /New printer/ }).click();
    const create = page.getByRole("dialog", { name: "New printer preset" });
    const createForm = create.getByRole("form", { name: "Create printer preset" });
    await createForm.getByLabel("Name").fill(name);
    await createForm.getByRole("combobox", { name: "Printer model" }).selectOption("Bambu Lab A1");
    await createForm.getByRole("combobox", { name: "Nozzle diameter" }).selectOption("0.6");
    await createForm.getByLabel(/Purchase cost/).fill("500");
    const presetWrite = page.waitForResponse(
      (response) =>
        response.url().endsWith("/printer-profiles") && response.request().method() === "POST",
    );
    await create.getByRole("button", { name: "Add preset" }).click();
    const presetReceipt = await presetWrite;
    expect(presetReceipt.status()).toBe(201);
    expect(await presetReceipt.json()).toMatchObject({
      printer_model: "Bambu Lab A1",
      nozzle_diameter_mm: 0.6,
      expenses: { lifetime_hours: 5000, power_watts: 95 },
    });

    const nameInput = await inputByValue(section, /^Printer preset name/, name);
    const editor = rowOf(nameInput);
    await editor.getByRole("combobox", { name: /^Printer model/ }).selectOption({
      label: "Custom printer model",
    });
    await editor.getByRole("textbox", { name: "Custom printer model" }).fill("Workshop CoreXY");
    await editor.getByRole("combobox", { name: /^Printer nozzle diameter/ }).selectOption({
      label: "Custom nozzle diameter (mm)",
    });
    await editor.getByRole("textbox", { name: "Custom nozzle diameter (mm)" }).fill("0.45");
    await editor.getByLabel("Expected lifetime (print hours)").fill("3000");
    await editor.getByLabel("Average power (W)").fill("132");
    const editWrite = page.waitForResponse(
      (response) =>
        /\/printer-profiles\/\d+$/.test(response.url()) && response.request().method() === "PATCH",
    );
    await editor.getByRole("button", { name: "Close", exact: true }).click();
    const editReceipt = await editWrite;
    expect(editReceipt.status()).toBe(200);
    expect(await editReceipt.json()).toMatchObject({
      printer_model: "Workshop CoreXY",
      nozzle_diameter_mm: 0.45,
      expenses: { lifetime_hours: 3000, power_watts: 132 },
    });

    await page.reload();
    await page.getByRole("tab", { name: /Printers/ }).click();
    const reloaded = await inputByValue(section, /^Printer preset name/, name);
    const persisted = rowOf(reloaded);
    await expect(persisted.getByLabel("Custom printer model", { exact: true })).toHaveValue(
      "Workshop CoreXY",
    );
    await expect(persisted.getByLabel("Custom nozzle diameter (mm)", { exact: true })).toHaveValue(
      "0.45",
    );
    await expect(persisted.getByLabel("Expected lifetime (print hours)")).toHaveValue("3000");
    await expect(persisted.getByLabel("Average power (W)")).toHaveValue("132");
    await rowOf(reloaded)
      .getByRole("button", { name: `Delete printer preset ${name}` })
      .click();
    await page
      .getByRole("dialog", { name: "Delete printer preset?" })
      .getByRole("button", { name: "Delete preset" })
      .click();
    // Counting inputs before reading each one races with the row disappearing.
    await expect(section.getByRole("region", { name: `Preset: ${name}`, exact: true })).toHaveCount(
      0,
    );
  });
  for (const kind of ["filament", "printer"] as const) {
    test(`two ${kind} preset editors recover a competing save`, async ({ page, browser }) => {
      const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
      const bundle = await authBundleFor(ADMIN.username, ADMIN.password);
      const headers = { Authorization: `Bearer ${bundle.token}` };
      const label = kind === "filament" ? "Filament" : "Printer";
      const name = `e2e-${kind}-conflict-${Date.now()}`;
      const response = await page.request.post(`${api}/api/v1/${kind}-profiles`, {
        headers,
        data: {
          name,
          ...(kind === "filament" ? { cost_per_kg: 25 } : { nozzle_diameter_mm: 0.4 }),
        },
      });
      expect(response.status()).toBe(201);
      const profile: { id: number } = await response.json();
      const { context, page: second } = await authedContext(browser, bundle);
      const numericLabel =
        kind === "filament" ? `Filament cost per kg ${profile.id}` : "Custom nozzle diameter (mm)";
      const nextValue = kind === "filament" ? "42" : "0.65";
      try {
        await page.goto("/profiles");
        await second.setViewportSize(DESKTOP_CHROMIUM.viewport);
        await second.goto(page.url());
        await expect(second.getByRole("heading", { name: "Profiles", exact: true })).toBeVisible();
        if (kind === "printer") {
          await page.getByRole("tab", { name: /Printers/ }).click();
          await second.getByRole("tab", { name: /Printers/ }).click();
        }
        await page
          .getByRole("region", { name: `Preset: ${name}`, exact: true })
          .getByRole("button")
          .click();
        await page.getByRole("button", { name: `Edit preset ${name}`, exact: true }).click();
        await second
          .getByRole("region", { name: `Preset: ${name}`, exact: true })
          .getByRole("button")
          .click();
        await second.getByRole("button", { name: `Edit preset ${name}`, exact: true }).click();
        if (kind === "printer") {
          await second
            .getByRole("combobox", { name: `Printer nozzle diameter ${profile.id}`, exact: true })
            .selectOption({ label: numericLabel });
        }
        // An invalid number captures the second editor's base without allowing
        // blur between browser tabs to save it before the competing edit.
        await second.getByRole("textbox", { name: numericLabel, exact: true }).fill("-1");
        await page
          .getByRole("textbox", { name: `${label} notes ${profile.id}`, exact: true })
          .fill("Keep the first editor notes");
        const firstSave = page.waitForResponse(
          (r) =>
            r.url().endsWith(`/api/v1/${kind}-profiles/${profile.id}`) &&
            r.request().method() === "PATCH",
        );
        await page
          .getByRole("dialog", { name: `Edit ${kind} preset`, exact: true })
          .getByRole("button", { name: "Close", exact: true })
          .click();
        expect((await firstSave).status()).toBe(200);
        await second.getByRole("textbox", { name: numericLabel, exact: true }).fill(nextValue);
        const staleSave = second.waitForResponse(
          (r) =>
            r.url().endsWith(`/api/v1/${kind}-profiles/${profile.id}`) &&
            r.request().method() === "PATCH",
        );
        await second
          .getByRole("dialog", { name: `Edit ${kind} preset`, exact: true })
          .getByRole("button", { name: "Close", exact: true })
          .click();
        expect((await staleSave).status()).toBe(412);
        await second.getByRole("button", { name: `Edit preset ${name}`, exact: true }).click();
        await expect(second.getByRole("textbox", { name: numericLabel, exact: true })).toHaveValue(
          nextValue,
        );
        await second.getByRole("button", { name: "Review current values" }).click();
        await expect(
          second
            .getByRole("status", { name: "Current preset values" })
            .getByText("Keep the first editor notes"),
        ).toBeVisible();
        const revisedSave = second.waitForResponse(
          (r) =>
            r.url().endsWith(`/api/v1/${kind}-profiles/${profile.id}`) &&
            r.request().method() === "PATCH",
        );
        await second.getByRole("button", { name: "Save revised changes" }).click();
        expect((await revisedSave).status()).toBe(200);
        await second.reload();
        if (kind === "printer") await second.getByRole("tab", { name: /Printers/ }).click();
        await second
          .getByRole("region", { name: `Preset: ${name}`, exact: true })
          .getByRole("button")
          .click();
        await second.getByRole("button", { name: `Edit preset ${name}`, exact: true }).click();
        await expect(second.getByRole("textbox", { name: numericLabel, exact: true })).toHaveValue(
          nextValue,
        );
        await expect(
          second.getByRole("textbox", { name: `${label} notes ${profile.id}`, exact: true }),
        ).toHaveValue("Keep the first editor notes");
      } finally {
        await context.close();
        await page.request.delete(`${api}/api/v1/${kind}-profiles/${profile.id}`, { headers });
      }
    });
  }
});
