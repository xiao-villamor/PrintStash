/** One cursor policy covers native controls, semantic controls and app routes.
 * Browser-computed styles catch Tailwind resets, cascade and inheritance drift.
 */
import { expect, test, type Page } from "@playwright/test";
import { useMockApi } from "./_setup";

useMockApi();

async function cursorFixture(page: Page) {
  await page.goto("/settings?section=imports");
  await expect(page.getByRole("heading", { name: "MakerWorld browser import" })).toBeVisible();
  await page.evaluate(() => {
    const panel = document.createElement("section");
    panel.setAttribute("aria-label", "Cursor policy fixture");
    panel.style.cssText =
      "position:fixed;inset:0;z-index:9999;overflow:auto;background:white;padding:20px;display:grid;gap:8px;";
    panel.innerHTML = `
      <button>Native action</button>
      <button><svg width="16" height="16" aria-hidden="true"><path d="M0 0h16v16H0z" /></svg><span>Nested action</span></button>
      <a href="#example">Example link</a>
      <div role="button" tabindex="0">Semantic action</div>
      <div role="tab" tabindex="0">Semantic tab</div>
      <div role="menuitem" tabindex="0">Semantic menu item</div>
      <button disabled>Disabled action</button>
      <button aria-disabled="true">ARIA disabled action</button>
      <div role="button" aria-disabled="true" tabindex="-1">Disabled semantic action</div>
      <div role="button" data-disabled="" tabindex="-1">Data disabled action</div>
      <fieldset disabled><button>Fieldset disabled action</button></fieldset>
      <fieldset disabled><legend><button>Enabled legend action</button></legend></fieldset>
      <div aria-disabled="true"><button>Inherited disabled action</button><button aria-disabled="false">Reenabled action</button></div>
      <button data-disabled="false">Data enabled action</button>
      <a href="#disabled" aria-disabled="true">Disabled link</a>
      <label class="cursor-action"><input type="checkbox" aria-label="Enabled checkbox">Enabled label</label>
      <label class="cursor-action"><input type="checkbox" aria-label="Disabled checkbox" disabled>Disabled label</label>
      <input type="radio" aria-label="Radio choice">
      <input type="range" aria-label="Range choice">
      <input type="file" aria-label="File choice">
      <input type="file" aria-label="Disabled file choice" disabled>
      <select aria-label="Select choice"><option>Choice</option></select>
      <details><summary>Disclosure</summary>Content</details>
      <input aria-label="Text editing">
      <textarea aria-label="Long text editing"></textarea>
      <button class="cursor-grab">Drag handle</button>
      <button class="cursor-col-resize">Resize handle</button>
      <span class="cursor-action">Custom action</span>
      <span class="cursor-disabled">Custom disabled action</span>
    `;
    document.body.append(panel);
  });
  return page.getByRole("region", { name: "Cursor policy fixture" });
}

test.describe("shared cursor policy", () => {
  for (const theme of ["light", "dark"]) {
    test(`uses the arrow for ordinary enabled controls in ${theme} theme`, async ({ page }) => {
      const panel = await cursorFixture(page);
      await page.evaluate(
        (theme) => document.documentElement.classList.toggle("dark", theme === "dark"),
        theme,
      );
      const controls = [
        panel.getByRole("button", { name: "Native action", exact: true }),
        panel.getByRole("button", { name: "Nested action", exact: true }),
        panel.getByRole("link", { name: "Example link" }),
        panel.getByRole("button", { name: "Semantic action", exact: true }),
        panel.getByRole("button", { name: "Enabled legend action" }),
        panel.getByRole("button", { name: "Reenabled action" }),
        panel.getByRole("button", { name: "Data enabled action" }),
        panel.getByRole("tab"),
        panel.getByRole("menuitem"),
        panel.getByRole("checkbox", { name: "Enabled checkbox" }),
        panel.getByRole("radio"),
        panel.getByRole("slider"),
        panel.getByLabel("File choice", { exact: true }),
        panel.getByRole("combobox"),
        panel.getByText("Disclosure", { exact: true }),
      ];
      for (const control of controls) {
        await control.hover();
        await expect(control).toHaveCSS("cursor", "default");
      }
      expect(
        await panel
          .getByLabel("File choice", { exact: true })
          .evaluate((element) => getComputedStyle(element, "::file-selector-button").cursor),
      ).toBe("default");
    });
  }

  test("uses the disabled cursor for unavailable controls", async ({ page }) => {
    const panel = await cursorFixture(page);
    for (const name of [
      "Disabled action",
      "ARIA disabled action",
      "Disabled semantic action",
      "Data disabled action",
      "Fieldset disabled action",
      "Inherited disabled action",
    ]) {
      await expect(panel.getByRole("button", { name, exact: true })).toHaveCSS("cursor", "default");
    }
    await expect(panel.getByRole("checkbox", { name: "Disabled checkbox" })).toHaveCSS(
      "cursor",
      "default",
    );
    await expect(panel.getByText("Disabled label", { exact: true })).toHaveCSS("cursor", "default");
    await expect(panel.getByRole("link", { name: "Disabled link" })).toHaveCSS("cursor", "default");
    expect(
      await panel
        .getByLabel("Disabled file choice")
        .evaluate((element) => getComputedStyle(element, "::file-selector-button").cursor),
    ).toBe("default");
  });

  test("inherits the action cursor through button content", async ({ page }) => {
    const panel = await cursorFixture(page);
    const label = panel.getByText("Nested action", { exact: true });
    await label.hover();
    await expect(label).toHaveCSS("cursor", "default");
    const icon = panel.getByRole("button", { name: "Nested action" }).locator("svg");
    await icon.hover();
    await expect(icon).toHaveCSS("cursor", "default");
  });

  test("changes all ordinary actions through one shared token", async ({ page }) => {
    const panel = await cursorFixture(page);
    await page.evaluate(() =>
      document.documentElement.style.setProperty("--cursor-action", "crosshair"),
    );
    for (const label of [
      "Native action",
      "Semantic action",
      "Custom action",
      "Enabled label",
      "Example link",
    ]) {
      await expect(panel.getByText(label, { exact: true })).toHaveCSS("cursor", "crosshair");
    }
    await expect(panel.getByRole("button", { name: "Disabled action", exact: true })).toHaveCSS(
      "cursor",
      "default",
    );
  });

  test("changes disabled cursors through their shared token", async ({ page }) => {
    const panel = await cursorFixture(page);
    await page.evaluate(() =>
      document.documentElement.style.setProperty("--cursor-disabled", "not-allowed"),
    );
    for (const label of [
      "Disabled action",
      "Disabled semantic action",
      "Custom disabled action",
      "Disabled label",
    ]) {
      await expect(panel.getByText(label, { exact: true })).toHaveCSS("cursor", "not-allowed");
    }
  });

  test("preserves specialized cursors", async ({ page }) => {
    const panel = await cursorFixture(page);
    await expect(panel.getByRole("textbox", { name: "Text editing", exact: true })).toHaveCSS(
      "cursor",
      "text",
    );
    await expect(panel.getByRole("textbox", { name: "Long text editing" })).toHaveCSS(
      "cursor",
      "text",
    );
    await expect(panel.getByRole("button", { name: "Drag handle" })).toHaveCSS("cursor", "grab");
    await expect(panel.getByRole("button", { name: "Resize handle" })).toHaveCSS(
      "cursor",
      "col-resize",
    );
  });

  for (const route of [
    "/",
    "/printers",
    "/profiles",
    "/statistics",
    "/inbox",
    "/documents",
    "/search",
    "/library/similar",
    ...[
      "imports",
      "access",
      "backup",
      "trash",
      "previews",
      "design",
      "work",
      "notifications",
      "spoolman",
      "libraries",
      "remote-storage",
      "ai-search",
      "maintenance",
      "sso",
      "about",
    ].map((section) => `/settings?section=${section}`),
  ]) {
    test(`all visible buttons follow the hover policy on ${route}`, async ({ page }, testInfo) => {
      await page.goto(route, { waitUntil: "networkidle" });
      await expect(page.getByRole("main").first()).toBeVisible();
      // TanStack's development-only launcher owns its styles and is absent
      // from the production app. Audit every user-facing action here.
      const buttons = page
        .getByRole("button")
        .and(page.locator(':visible:not([aria-label="Open Tanstack query devtools"])'));
      await expect(buttons.first()).toBeVisible();
      let checked = 0;
      for (const button of await buttons.elementHandles()) {
        if (!(await button.isVisible())) continue;
        // Card actions become interactive when the pointer enters the card.
        // Move there first, then retain the ordinary hover actionability check.
        await button.scrollIntoViewIfNeeded();
        // Center controls in their scrolling surface so sticky group headers do not cover them.
        await button.evaluate((element) => {
          if (!(element instanceof HTMLElement)) throw new Error("Expected an HTML control");
          element.scrollIntoView({ block: "center", inline: "nearest" });
        });
        const box = await button.boundingBox();
        if (box) await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
        const label = (await button.getAttribute("aria-label")) ?? (await button.textContent());
        const disabledBeforeHover = await button.isDisabled();
        await test.step(`Hover ${label}`, () => button.hover({ force: disabledBeforeHover }));
        // A loading action may become enabled while Playwright moves to it.
        // Assert the state at hover time, not the earlier loading snapshot.
        const disabled = await button.isDisabled();
        const allowed = disabled ? ["default"] : ["default", "grab", "grabbing", "col-resize"];
        const cursor = await button.evaluate((element) => {
          if (!(element instanceof Element)) throw new Error("Expected a control element");
          return getComputedStyle(element).cursor;
        });
        const description = await button.evaluate((element) => {
          if (!(element instanceof Element)) throw new Error("Expected a control element");
          return element.outerHTML.slice(0, 500);
        });
        expect(allowed, `${route}: ${description}`).toContain(cursor);
        checked++;
      }
      expect(checked).toBeGreaterThan(0);
      await testInfo.attach("cursor-audit", {
        body: JSON.stringify({ route, checked }),
        contentType: "application/json",
      });
    });
  }
});
