/**
 * Filament and printer presets — the values every cost estimate is computed from.
 *
 * A preset is edited once and read by every print afterwards, so an edit that appears to
 * save and does not is a whole library of wrong numbers. Both flows here therefore create,
 * edit, and delete rather than stopping at the first green tick.
 */
import { type Locator } from "@playwright/test";
import { test, expect, authBundleFor, ADMIN } from "./helpers";
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
      const form = page.getByRole("form", { name: `Create ${kind} preset` });
      for (const field of await form.getByRole("textbox").all()) await field.fill("12");

      await form.getByRole("button", { name: "Cancel", exact: true }).click();

      await expect(form).toHaveCount(0);
      await expect(opener).toBeFocused();
      await opener.press("Enter");
      for (const field of await form.getByRole("textbox").all())
        await expect(field).toHaveValue("");
      await expect(form.getByRole("button", { name: "Add preset", exact: true })).toBeDisabled();
      expect(writes).toEqual([]);
      await form.getByRole("button", { name: "Cancel", exact: true }).click();
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
      const form = page.getByRole("form", { name: "Create filament preset" });
      await form.getByLabel("Name").fill(value);
      await form.getByLabel("Cost per kg").fill("25");
      await form.getByRole("button", { name: "Add preset" }).click();
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
    await Promise.all([
      page.waitForResponse(
        (response) =>
          /\/api\/v1\/filament-profiles\/\d+/.test(response.url()) &&
          response.request().method() === "PATCH",
      ),
      page.getByRole("dialog", { name: "Edit filament preset" }).getByRole("heading").click(),
    ]);
    await expect(cost).toBeEnabled();
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

  test("create and delete a printer preset", async ({ page }) => {
    const name = `e2e-printer-${Date.now()}`;
    await page.goto("/profiles");

    await page.getByRole("tab", { name: /Printers/ }).click();
    const section = page.locator("section", { hasText: "Printer presets" });
    await page.getByRole("button", { name: /New printer/ }).click();
    const createForm = page.getByRole("form", { name: "Create printer preset" });
    await createForm.getByLabel("Name").fill(name);
    await createForm.getByLabel("Printer model").fill("Voron 2.4");
    await createForm.getByRole("button", { name: "Add preset" }).click();

    const nameInput = await inputByValue(section, /^Printer preset name/, name);
    await expect(nameInput).toBeVisible();

    await page.reload();
    await page.getByRole("tab", { name: /Printers/ }).click();
    const reloaded = await inputByValue(section, /^Printer preset name/, name);
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
    test(`two ${kind} preset editors recover a competing save`, async ({ page }) => {
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
      const second = await page.context().newPage();
      const numericLabel =
        kind === "filament"
          ? `Filament cost per kg ${profile.id}`
          : `Printer nozzle diameter ${profile.id}`;
      const nextValue = kind === "filament" ? "42" : "0.6";
      try {
        await page.goto("/profiles");
        await second.goto("/profiles");
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
          .getByRole("heading")
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
          .getByRole("heading")
          .click();
        expect((await staleSave).status()).toBe(412);
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
        await second.close();
        await page.request.delete(`${api}/api/v1/${kind}-profiles/${profile.id}`, { headers });
      }
    });
  }
});
