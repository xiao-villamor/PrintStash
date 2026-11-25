/*
 * The printer list and one printer's detail page, through the frontend proxy.
 *
 * Both go through the proxy rather than straight at the API, and that is what is
 * under test: a proxy path that stops resolving gives an empty fleet with no
 * error, which reads as "no printers configured" to a user who has several.
 *
 * The detail route is parametrized, so the dynamic id has to survive the render.
 * An id that arrives as `NaN` produces a page that loads, looks right, and shows
 * live status for nothing.
 */
import { expect, test, type WebSocketRoute } from "@playwright/test";
import type { PrinterSnapshot } from "@/types";
import { aPrinter } from "../../src/test-support/factories";

import { collectPageProblems, useMockApi } from "./_setup";
import { collapseNavigationSidebar } from "../e2e-real/util";

useMockApi();

test.describe("printer routes", () => {
  test("remembers the grouped Fleet layout", async ({ page }, testInfo) => {
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.route("**/api/v1/printers", (route) =>
      route.fulfill({
        json: [
          aPrinter({ id: 1, name: "Lab Voron", group: "Lab", status: "error" }),
          aPrinter({ id: 2, name: "Lab P1S", group: "Lab", status: "ready" }),
          aPrinter({ id: 3, name: "Demo printer", group: "Routing demo", status: "ready" }),
          aPrinter({ id: 4, name: "Standalone printer", group: null, status: "ready" }),
        ],
      }),
    );
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    const toggle = page.getByRole("button", { name: "Group printers", exact: true });
    const lab = page.getByRole("region", { name: "Lab", exact: true });
    const demo = page.getByRole("region", { name: "Routing demo", exact: true });
    const ungrouped = page.getByRole("region", { name: "Ungrouped", exact: true });

    await expect(toggle).toHaveAttribute("aria-pressed", "false");
    await expect(lab).toHaveCount(0);
    await toggle.click();
    await expect(lab.getByRole("link", { name: "Lab Voron" })).toBeVisible();
    await expect(lab.getByRole("link", { name: "Lab P1S" })).toBeVisible();
    await expect(demo.getByRole("link", { name: "Demo printer" })).toBeVisible();
    await expect(ungrouped.getByRole("link", { name: "Standalone printer" })).toBeVisible();
    await expect(lab).toHaveCSS("border-top-style", "dotted");
    await expect(lab).toHaveCSS("border-top-width", "2px");
    const frame = await lab.boundingBox();
    const nextFrame = await demo.boundingBox();
    const lastCard = await lab.getByRole("article").last().boundingBox();
    const label = await lab.getByRole("heading", { name: "Lab 2" }).boundingBox();
    if (!frame || !nextFrame || !lastCard || !label)
      throw new Error("The group frame, label or card was missing");
    const inset = frame.x + frame.width - (lastCard.x + lastCard.width);
    expect(inset).toBeGreaterThan(8);
    expect(inset).toBeLessThan(24);
    expect(Math.abs(label.y + label.height / 2 - frame.y)).toBeLessThanOrEqual(2);
    expect(lastCard.y - frame.y).toBeLessThan(24);
    expect(nextFrame.y).toBe(frame.y);
    expect(nextFrame.x).toBeGreaterThan(frame.x + frame.width);
    await page.screenshot({ path: testInfo.outputPath("grouped-printer-fleet.png") });

    await page.getByRole("combobox", { name: "Group", exact: true }).selectOption({ label: "Lab" });
    await expect(lab.getByRole("link", { name: "Lab Voron" })).toBeVisible();
    await expect(demo).toHaveCount(0);
    await page.reload();
    await expect(toggle).toHaveAttribute("aria-pressed", "true");
    await expect(ungrouped.getByRole("link", { name: "Standalone printer" })).toBeVisible();
    await toggle.click();
    await expect(ungrouped).toHaveCount(0);
    await expect(page.getByRole("link", { name: "Standalone printer" })).toBeVisible();
  });

  test("preserves destination usability across send modes", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.route("**/api/v1/printers", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        json: [
          ...(await response.json()),
          aPrinter({ id: 77, name: "Workshop printer", group: "Workshop" }),
        ],
      });
    });
    await page.route("**/api/v1/models/1", async (route) => {
      const response = await route.fetch();
      const model = await response.json();
      const revision = model.files.find(
        (file: { file_type: string }) => file.file_type === "gcode",
      );
      revision.original_filename = "workshop-case.gcode.3mf";
      await route.fulfill({ response, json: model });
    });
    await page.route("**/api/v1/files/*/gcode-plates", (route) =>
      route.fulfill({
        json: {
          plates: [1, 2],
          identities: [
            { number: 1, name: "Enclosure base", object_count: 1 },
            { number: 2, name: "Enclosure lid and mounting clips", object_count: 3 },
          ],
          plate_metadata: [
            {
              number: 1,
              metadata: { estimated_time_s: 6480, filament_weight_g: 97.4, filament_cost: null },
            },
            {
              number: 2,
              metadata: { estimated_time_s: 2820, filament_weight_g: 38.2, filament_cost: null },
            },
          ],
        },
      }),
    );
    await page.goto("/models/1?tab=revisions");
    const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    await page.getByRole("button", { name: "Prepare print", exact: true }).first().click();
    const direct = page.getByRole("dialog", { name: /^Send Rev/ });
    await direct.getByRole("radio", { name: /^Plate 02/ }).check();
    const firstFrame = (await direct.boundingBox())!;
    const firstFooter = (await direct.getByRole("button", { name: "Cancel" }).boundingBox())!;
    const mode = direct.getByRole("radio", { name: "Add to queue", exact: true });
    const firstSwitch = (await mode.boundingBox())!;
    const start = direct.getByRole("checkbox", { name: "Start print immediately" });
    await expect(start).toHaveCSS("width", "20px");
    await expect(start).toHaveCSS("height", "20px");
    const directDestination = direct.getByRole("button", { name: "Destination", exact: true });
    await directDestination.click();
    const picker = page.getByRole("dialog", { name: "Destination", exact: true });
    const search = picker.getByRole("combobox", { name: "Search" });
    await expect(search).toBeFocused();
    const searchBox = (await search.boundingBox())!;
    const pickerBox = (await picker.boundingBox())!;
    expect(searchBox.x - pickerBox.x).toBeGreaterThanOrEqual(8);
    await search.fill("Workshop");
    const workshop = picker.getByRole("option", { name: /^Workshop printer/ });
    await workshop.click();
    await expect(workshop).toHaveAttribute("aria-selected", "true");
    await picker.getByRole("button", { name: "Done", exact: true }).click();
    const directSelection = await directDestination.innerText();
    expect(directSelection).toContain("Workshop printer");
    expect(
      await direct
        .locator(".queue-compose-body")
        .evaluate((body) => body.scrollHeight - body.clientHeight),
    ).toBeLessThanOrEqual(1);

    await mode.click();
    const queue = page.getByRole("dialog", { name: "Add to queue", exact: true });
    expect(await queue.boundingBox()).toEqual(firstFrame);
    const queueFooter = (await queue.getByRole("button", { name: "Cancel" }).boundingBox())!;
    expect(queueFooter.y).toBeCloseTo(firstFooter.y, 0);
    const queueSwitch = (await queue
      .getByRole("radio", { name: "Add to queue", exact: true })
      .boundingBox())!;
    expect(queueSwitch.y).toBeCloseTo(firstSwitch.y, 0);
    const compatibility = queue.getByRole("checkbox", {
      name: "Allow material or nozzle mismatch",
    });
    await expect(compatibility).toHaveCSS("width", "20px");
    await expect(compatibility).toHaveCSS("height", "20px");
    const group = queue.getByRole("combobox", { name: "Target group", exact: true });
    await group.selectOption("Workshop");
    await group.focus();
    const groupLabel = (await queue.getByText("Target group", { exact: true }).boundingBox())!;
    expect(groupLabel.y + groupLabel.height).toBeLessThan((await group.boundingBox())!.y);
    await queue.getByRole("radio", { name: "Choose printer", exact: true }).click();
    const queuedDestination = queue.getByRole("button", { name: "Destination", exact: true });
    await queuedDestination.click();
    await expect(picker.getByRole("option")).toHaveCount(1);
    await workshop.click();
    await expect(picker).toHaveCount(0);
    await expect(queuedDestination).toContainText("Workshop printer");
    await expect(queuedDestination).toBeFocused();
    await expect(queue.getByRole("radio", { name: /^Plate 02/ })).toBeChecked();
    expect(
      await queue
        .locator(".queue-compose-body")
        .evaluate((body) => body.scrollHeight - body.clientHeight),
    ).toBeLessThanOrEqual(1);

    await queue.getByRole("radio", { name: "Send now", exact: true }).click();
    await expect(directDestination).toHaveText(directSelection);
  });

  test("keeps the queue dialog consistent across entry points", async ({ page }) => {
    await page.route("**/api/v1/printers", async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        json: [...(await response.json()), aPrinter({ id: 77, name: "Workshop printer" })],
      });
    });
    const widths: number[] = [];
    for (const route of ["/models/1?tab=revisions", "/printers?tab=queue"]) {
      await page.goto(route);
      const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
      if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
      await expect(sidebar).toHaveAttribute("aria-expanded", "false");
      if (route.startsWith("/models")) {
        await page.getByRole("button", { name: "Prepare print", exact: true }).click();
        await page.getByRole("radio", { name: "Add to queue", exact: true }).click();
      } else {
        await page.getByRole("button", { name: "Add to queue", exact: true }).first().click();
      }
      const dialog = page.getByRole("dialog", { name: "Add to queue", exact: true });
      const details = dialog.getByRole("complementary", { name: "Dispatch details" });
      await expect(details).toBeVisible();
      widths.push((await dialog.boundingBox())!.width);
      await dialog.getByRole("radio", { name: "Choose printer", exact: true }).check();
      await dialog.getByRole("button", { name: "Destination", exact: true }).click();
      await page
        .getByRole("dialog", { name: "Destination", exact: true })
        .getByRole("option", { name: /^Workshop printer\b/ })
        .click();
      await expect(details.getByText("Workshop printer", { exact: true })).toBeVisible();
      await dialog.getByRole("radio", { name: "Rush", exact: true }).check();
      await expect(details.getByText("Rush", { exact: true })).toBeVisible();
      const mismatch = dialog.getByRole("checkbox", {
        name: "Allow material or nozzle mismatch",
      });
      await mismatch.click();
      await expect(details.getByText("Allow mismatch", { exact: true })).toBeVisible();
      const submit = dialog.getByRole("button", { name: "Add to queue", exact: true });
      await expect(submit).toBeInViewport();
      if (route.startsWith("/models")) {
        const receipt = page.waitForRequest(
          (request) => request.method() === "POST" && request.url().endsWith("/api/v1/fleet/queue"),
        );
        await submit.click();
        expect((await receipt).postDataJSON()).toMatchObject({
          strategy: "manual",
          printer_id: 77,
          priority: "rush",
          compatibility_policy: "allow_mismatch",
        });
      } else {
        await dialog.getByRole("button", { name: "Cancel", exact: true }).click();
      }
      await expect(dialog).toHaveCount(0);
    }
    expect(widths).toEqual([960, 960]);
  });

  test("shares one visible radio control across print workflows", async ({ page }) => {
    const scenarios = [
      ["/models/1?tab=history", "Record result", "Result", "Success", "Failed"],
      ["/printers?tab=queue", "Add to queue", "Priority", "Normal", "Rush"],
    ];
    for (const [route, action, group, initial, next] of scenarios) {
      await page.goto(route);
      const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
      if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
      await expect(sidebar).toHaveAttribute("aria-expanded", "false");
      await page.getByRole("button", { name: action, exact: true }).first().click();
      const options = page.getByRole("dialog").getByRole("group", { name: group, exact: true });
      const radio = options.getByRole("radio", { name: initial, exact: true });
      await expect(radio).toBeChecked();
      await expect(radio).toHaveCSS("opacity", "1");
      await expect(radio).toHaveCSS("width", "16px");
      await expect(radio.locator("xpath=..")).toHaveCSS("border-top-width", "0px");
      await radio.focus();
      await page.keyboard.press("ArrowRight");
      const selected = options.getByRole("radio", { name: next, exact: true });
      await expect(selected).toBeChecked();
      await expect(selected).toBeFocused();
      await expect(radio).not.toBeChecked();
      await page.keyboard.press("Escape");
    }
  });

  for (const theme of ["dark", "light"] as const) {
    test(`shows a readable compatibility checkbox in ${theme} mode`, async ({ page }) => {
      await page.addInitScript((mode) => localStorage.setItem("printstash.theme", mode), theme);
      await page.goto("/printers?tab=queue");
      const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
      if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
      await expect(sidebar).toHaveAttribute("aria-expanded", "false");
      await page.getByRole("button", { name: "Add to queue", exact: true }).first().click();
      const dialog = page.getByRole("dialog", { name: "Add to queue" });
      const label = "Allow material or nozzle mismatch";
      const checkbox = dialog.getByRole("checkbox", { name: label });
      const details = dialog.getByRole("complementary", { name: "Dispatch details" });
      await expect(checkbox).toBeVisible();
      await expect(checkbox).not.toBeChecked();
      await expect(checkbox).toHaveCSS("width", "20px");
      await expect(checkbox).toHaveCSS("height", "20px");

      await dialog.getByText(label, { exact: true }).click();
      await expect(checkbox).toBeChecked();
      await expect(details.getByText("Allow mismatch", { exact: true })).toBeVisible();

      await checkbox.press("Space");
      await expect(checkbox).toBeFocused();
      await expect(checkbox).not.toBeChecked();
      await expect(details.getByText("Require compatible material")).toBeVisible();
    });
  }

  test("fits a long saved revision through the queue selection flow", async ({ page }) => {
    const name = "ContinuousWorkshopModel".repeat(9);
    const label = "ContinuousRevisionLabel".repeat(5);
    await page.route("**/api/v1/models/page?**", async (route) => {
      const response = await route.fetch();
      const result = await response.json();
      result.items = result.items.map((item: { id: number }) =>
        item.id === 1 ? { ...item, name } : item,
      );
      await route.fulfill({ response, json: result });
    });
    await page.route("**/api/v1/models/1", async (route) => {
      const response = await route.fetch();
      const model = await response.json();
      model.name = name;
      model.files[1].revision_label = label;
      model.files.push(
        ...Array.from({ length: 11 }, (_, index) => ({
          ...model.files[1],
          id: 100 + index,
          gcode_revision_number: 2 + index,
          revision_label: null,
          original_filename: `other-part-${index + 1}.gcode`,
        })),
      );
      await route.fulfill({ response, json: model });
    });
    await page.setViewportSize({ width: 800, height: 650 });
    await page.goto("/printers?tab=queue");
    await page.getByRole("button", { name: "Add to queue", exact: true }).first().click();
    await page.getByText("Browse saved Revisions", { exact: true }).click();
    const picker = page.getByRole("dialog", { name: "Revision", exact: true });
    await picker.getByRole("button", { name, exact: true }).click();
    await picker.getByRole("searchbox", { name: "Search revisions" }).fill(label);
    const revision = picker.getByRole("button", { name: new RegExp(label) });
    await expect(revision).toBeVisible();
    await expect
      .poll(() => revision.evaluate((element) => element.scrollWidth - element.clientWidth))
      .toBeLessThanOrEqual(1);
    await revision.click();
    await expect(picker).toBeHidden();
    const dialog = page.getByRole("dialog", { name: "Add to queue" });
    await expect(dialog.getByText(name, { exact: true })).toBeVisible();
    await expect
      .poll(() =>
        dialog.locator(".queue-compose-body").evaluate((element) => {
          return element.scrollWidth - element.clientWidth;
        }),
      )
      .toBeLessThanOrEqual(1);
    await expect(dialog.getByRole("button", { name: "Add to queue" })).toBeInViewport();
  });

  test("printer list route loads configured printers through the frontend proxy", async ({
    page,
  }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers");

    await expect(page.getByRole("heading", { name: "Printers" })).toBeVisible();
    await expect(page.getByRole("link", { name: /ender/i })).toBeVisible();
    await expect(page.getByText(/^Moonraker(?: ·.*)?$/)).toBeVisible();
    await expect(page.getByText("ready", { exact: true }).first()).toBeVisible();
    await expect(page.getByLabel("Fleet summary").getByText("Ready")).toBeVisible();
    await expect(page.getByText("No printers configured yet.")).toHaveCount(0);
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    expect(problems).toEqual([]);
  });

  for (const theme of ["dark", "light"] as const) {
    test(`uses a theme-appropriate printer artwork background in ${theme} mode`, async ({
      page,
    }) => {
      await page.addInitScript((mode) => {
        localStorage.setItem("printstash.theme", mode);
        localStorage.setItem("printstash.printer-card.show-image", "true");
      }, theme);
      await page.route("**/api/v1/printers", async (route) => {
        const response = await route.fetch();
        const [printer] = await response.json();
        await route.fulfill({ response, json: [{ ...printer, model_name: "Bambu Lab P1S" }] });
      });
      await page.goto("/printers");
      const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
      if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
      const artwork = page.getByRole("img", { name: "Bambu Lab P1S printer" });
      await expect(artwork).toBeVisible();
      const pane = artwork.locator("..");
      await pane.screenshot({ path: test.info().outputPath(`printer-artwork-${theme}.png`) });
      const color = await page.evaluate((mode) => {
        const sample = document.createElement("div");
        sample.style.backgroundColor =
          mode === "dark" ? "var(--muted)" : "var(--preview-image-matte)";
        document.body.append(sample);
        const value = getComputedStyle(sample).backgroundColor;
        sample.remove();
        return value;
      }, theme);
      await expect(pane).toHaveCSS("background-color", color);
    });
  }

  test("marks a printer error with the destructive color on the farm map", async ({ page }) => {
    await page.route("**/api/v1/fleet/summary", async (route) => {
      const response = await route.fetch();
      const summary = await response.json();
      await route.fulfill({
        response,
        json: {
          ...summary,
          printers: [{ printer_id: 3, name: "ender", status: "error", drain_mode: true }],
        },
      });
    });
    await page.goto("/printers?tab=queue");
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await toggle.getAttribute("aria-expanded")) === "true") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const node = page.getByRole("button", { name: /Error ender/ });
    await expect(node).toBeVisible();
    const error = node.getByText("Error", { exact: true });
    const destructive = await error.evaluate((element) => {
      const swatch = document.createElement("span");
      swatch.style.color = "var(--destructive)";
      element.append(swatch);
      const color = getComputedStyle(swatch).color;
      swatch.remove();
      return color;
    });
    await expect(error).toHaveCSS("color", destructive);
  });

  test("updates the Fleet card when a print starts without manual refresh", async ({ page }) => {
    let printing = false;
    await page.route("**/api/v1/fleet/summary", async (route) => {
      const response = await route.fetch();
      const summary = await response.json();
      await route.fulfill({
        response,
        json: {
          ...summary,
          printers: [
            {
              printer_id: 3,
              name: "ender",
              status: printing ? "printing" : "ready",
              current_job_name: printing ? "live-print.gcode" : null,
              progress: printing ? 0.35 : null,
              loaded_slots: [],
            },
          ],
        },
      });
    });
    await page.goto("/printers");
    await collapseNavigationSidebar(page);
    const card = page.getByRole("article").filter({
      has: page.getByRole("link", { name: "ender", exact: true }),
    });
    await expect(card.getByText("No active print")).toBeVisible();
    printing = true;
    await expect(card.getByText("live-print.gcode", { exact: true })).toBeVisible();
    await expect(card.getByText("printing", { exact: true })).toBeVisible();
    await expect(card.getByText("35%", { exact: true })).toBeVisible();
    printing = false;
    await expect(card.getByText("ready", { exact: true })).toBeVisible();
    await expect(card.getByText("No active print")).toBeVisible();
  });

  test("animates the live farm route beside waiting jobs", async ({ page }, testInfo) => {
    let printing = false;
    await page.addInitScript(() => localStorage.setItem("printstash.theme", "dark"));
    await page.route("**/api/v1/fleet/summary", async (route) => {
      const response = await route.fetch();
      const summary = await response.json();
      await route.fulfill({
        response,
        json: {
          ...summary,
          printers: [
            {
              printer_id: 3,
              name: "ender",
              status: printing ? "printing" : "ready",
              current_job_name: printing ? "live-print.gcode" : null,
              progress: printing ? 0.35 : null,
            },
          ],
        },
      });
    });
    await page.route("**/api/v1/fleet/queue?**", async (route) => {
      const response = await route.fetch();
      const [job] = await response.json();
      await route.fulfill({
        response,
        json: [{ ...job, state: "queued", remote_filename: "waiting.gcode" }],
      });
    });
    await page.goto("/printers?tab=queue");
    await collapseNavigationSidebar(page);
    const waiting = page.locator('.react-flow__node[data-id="job-156"]');
    const activeRoute = page.locator(".routing-link-printing .react-flow__edge-path").last();
    await expect(waiting).toBeVisible();
    await expect(page.getByRole("button", { name: /Ready ender/ })).toBeVisible();
    await expect(activeRoute).toHaveCount(0);

    printing = true;
    await expect(page.getByRole("button", { name: /Printing ender/ })).toBeVisible();
    await expect(waiting).toBeVisible();
    await expect(activeRoute).toHaveCSS("animation-name", "routing-flow");
    await expect
      .poll(() =>
        waiting.evaluate((node) => {
          const map = node.closest(".react-flow");
          return !!map && node.getBoundingClientRect().bottom <= map.getBoundingClientRect().bottom;
        }),
      )
      .toBe(true);
    await page.screenshot({ path: testInfo.outputPath("live-farm-routing.png") });
    printing = false;
    await expect(activeRoute).toHaveCount(0);
  });

  test("colors the fleet counts that need an operator’s attention", async ({ page }) => {
    await page.route("**/api/v1/printers", async (route) => {
      const response = await route.fetch();
      const [printer] = await response.json();
      await route.fulfill({ response, json: [{ ...printer, status: "error" }] });
    });
    await page.goto("/printers");
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await toggle.getAttribute("aria-expanded")) === "true") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    const fleet = page.getByRole("region", { name: "Fleet summary" });
    const attention = fleet
      .getByRole("button", { name: "1 Needs attention" })
      .getByText("1", { exact: true });
    const ready = fleet.getByRole("button", { name: "0 Ready" }).getByText("0", { exact: true });
    await expect(attention).toBeVisible();
    await expect(ready).toBeVisible();
    expect(await attention.evaluate((element) => getComputedStyle(element).color)).not.toBe(
      await ready.evaluate((element) => getComputedStyle(element).color),
    );
  });

  test("fleet cards contain telemetry when a job has a long filename", async ({ page }) => {
    const currentJob = `workshop-${"continuous-filename-".repeat(10)}.gcode.3mf`;
    await page.addInitScript(() =>
      localStorage.setItem("printstash.printer-card.show-image", "true"),
    );
    await page.route("**/api/v1/fleet/summary", async (route) => {
      const response = await route.fetch();
      const summary = await response.json();
      await route.fulfill({
        response,
        json: {
          ...summary,
          printers: [
            ...summary.printers.filter(
              (printer: { printer_id: number }) => printer.printer_id !== 3,
            ),
            {
              printer_id: 3,
              status: "ready",
              current_job_id: 1,
              current_job_name: currentJob,
              progress: 0.37,
              telemetry: { nozzle_c: 218, bed_c: 65, part_fan_speed: 0.7 },
              nozzle_diameter_mm: 0.4,
              loaded_slots: [],
            },
          ],
        },
      });
    });
    await page.route("**/api/v1/printers", async (route) => {
      const response = await route.fetch();
      const [printer] = await response.json();
      await route.fulfill({
        response,
        json: [
          { ...printer, model_name: "Creality Ender 3" },
          { ...printer, id: 4, name: "Workshop Bambu", model_name: "Bambu Lab P1S" },
        ],
      });
    });
    await page.goto("/printers");
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await toggle.getAttribute("aria-expanded")) === "false") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");

    const card = page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: "ender", exact: true }) });
    const job = card.getByText(currentJob, { exact: true });
    const temperature = card.getByText("218 °C", { exact: true });
    await expect(job).toHaveAttribute("title", currentJob);
    await expect(temperature).toBeVisible();
    const activity = page.getByRole("region", { name: "Fleet activity", exact: true });
    const lastCard = page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: "Workshop Bambu", exact: true }) });

    for (const width of [1920, 1280, 720]) {
      if (width === 720) await toggle.click();
      await page.setViewportSize({ width, height: width === 1920 ? 1080 : 780 });
      await expect
        .poll(() => card.evaluate((element) => element.scrollWidth - element.clientWidth))
        .toBeLessThanOrEqual(1);
      const cardBox = (await card.boundingBox())!;
      const temperatureBox = (await temperature.boundingBox())!;
      const activityBox = (await activity.boundingBox())!;
      const lastCardBox = (await lastCard.boundingBox())!;
      expect(temperatureBox.x + temperatureBox.width).toBeLessThan(cardBox.x + cardBox.width);
      expect(lastCardBox.x + lastCardBox.width).toBeLessThanOrEqual(width);
      expect(activityBox.x + activityBox.width).toBeLessThanOrEqual(width);
      if (width < 1920) {
        expect(activityBox.y).toBeGreaterThan(lastCardBox.y + lastCardBox.height);
      }
    }
  });

  test("fleet cards open printers from the artwork or card body", async ({ page }) => {
    await page.addInitScript(() =>
      localStorage.setItem("printstash.printer-card.show-image", "true"),
    );
    await page.route("**/api/v1/fleet/summary", async (route) => {
      const response = await route.fetch();
      const summary = await response.json();
      await route.fulfill({
        response,
        json: {
          ...summary,
          printers: [
            {
              printer_id: 3,
              status: "ready",
              telemetry: { nozzle_c: 72, bed_c: 45, part_fan_speed: 0 },
              nozzle_diameter_mm: 0.4,
              loaded_slots: [],
              loaded_material_slots: [
                {
                  slot_key: "tool0",
                  label: "Front feed",
                  material_type: "PLA",
                  color_hex: "#4169E1",
                },
              ],
            },
          ],
        },
      });
    });
    await page.route("**/api/v1/printers", async (route) => {
      const response = await route.fetch();
      const [printer] = await response.json();
      await route.fulfill({
        response,
        json: [
          { ...printer, model_name: "Creality Ender 3" },
          { ...printer, id: 4, name: "Workshop Bambu", model_name: "Bambu Lab P1S" },
        ],
      });
    });
    await page.goto("/printers");
    const toggle = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await toggle.getAttribute("aria-expanded")) === "true") await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(page.locator("#collections-pane")).toHaveCSS("width", "0px");

    const card = page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: "ender", exact: true }) });
    const second = page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: "Workshop Bambu" }) });
    const activity = page.getByRole("region", { name: "Fleet activity" });
    await expect(activity.getByText("interrupted-part.gcode")).toBeVisible();
    const firstBox = await card.boundingBox();
    const secondBox = await second.boundingBox();
    const railBox = await activity.boundingBox();
    expect(firstBox).not.toBeNull();
    expect(secondBox).not.toBeNull();
    expect(railBox).not.toBeNull();
    expect(firstBox!.y).toBeCloseTo(secondBox!.y, 0);
    expect(firstBox!.width).toBeCloseTo(secondBox!.width, 0);
    expect(secondBox!.x + secondBox!.width).toBeLessThan(railBox!.x);
    expect(railBox!.x + railBox!.width).toBeLessThanOrEqual(1920);
    await expect(card.getByText("Image: OrcaSlicer")).toHaveCount(0);

    const art = card.getByRole("img");
    const fan = card.getByText("Part cooling fan", { exact: true });
    const fanIcon = fan.locator("svg");
    const image = await art.boundingBox();
    const identity = await card.getByRole("heading", { name: "ender" }).boundingBox();
    expect(image?.height).toBeGreaterThanOrEqual(120);
    expect(image!.width).toBeLessThan(firstBox!.width / 3);
    expect(image!.x + image!.width).toBeLessThan(identity!.x);
    expect(identity!.y).toBeLessThan(image!.y + image!.height);
    await page.mouse.move(image!.x + image!.width / 2, image!.y + image!.height / 2);
    await expect(art).not.toHaveCSS("translate", "none");
    await expect(fanIcon).toHaveCSS("transition-property", /transform/);
    await page.emulateMedia({ reducedMotion: "reduce" });
    await expect(art).toHaveCSS("translate", "none");
    await expect(fanIcon).toHaveCSS("transition-property", "none");
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await page.mouse.click(image!.x + image!.width / 2, image!.y + image!.height / 2);
    await expect(page).toHaveURL(/\/printers\/3$/);

    await page.goBack();
    await page
      .getByRole("article")
      .filter({ has: page.getByRole("link", { name: "ender", exact: true }) })
      .click({ button: "right" });
    await page.getByRole("menuitem", { name: "Edit connection", exact: true }).click();
    const settings = page.getByRole("dialog", { name: "Edit printer connection" });
    await expect(settings).toBeVisible();
    await expect(page).toHaveURL(/\/printers$/);
    await page.keyboard.press("Escape");
    await expect(settings).toBeHidden();

    await card.click({ button: "right" });
    await page.getByRole("menuitem", { name: "Set model", exact: true }).click();
    await expect(page.getByRole("dialog", { name: "Choose printer model" })).toBeVisible();
    await expect(page).toHaveURL(/\/printers$/);
    await page.keyboard.press("Escape");

    const link = card.getByRole("link", { name: "ender", exact: true });
    const newTab = page.context().waitForEvent("page");
    await link.click({ modifiers: ["ControlOrMeta"] });
    const opened = await newTab;
    await expect(opened).toHaveURL(/\/printers\/3$/);
    await opened.close();
    await expect(page).toHaveURL(/\/printers$/);

    await expect(fan).toBeVisible();
    await expect(card.getByText("0%", { exact: true })).toBeVisible();
    await expect(second.getByText("Part cooling fan")).toHaveCount(0);
    const reading = await fan.boundingBox();
    expect(reading).not.toBeNull();
    await page.mouse.click(reading!.x + reading!.width / 2, reading!.y + reading!.height / 2);
    await expect(page).toHaveURL(/\/printers\/3$/);

    await page.goBack();
    const feed = await card.getByText("Front feed").boundingBox();
    expect(feed).not.toBeNull();
    await page.mouse.click(feed!.x + feed!.width / 2, feed!.y + feed!.height / 2);
    await expect(page).toHaveURL(/\/printers\/3$/);

    await page.goBack();
    await link.press("Enter");
    await expect(page).toHaveURL(/\/printers\/3$/);
  });

  test("printer detail route preserves the dynamic id and renders live status", async ({
    page,
  }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers/3");

    await expect(page.getByRole("heading", { name: "ender" })).toBeVisible();
    const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText(/^Moonraker ·/)).toBeVisible();
    await expect(page.getByRole("heading", { name: "Temperatures", exact: true })).toBeVisible();
    await page.getByRole("tab", { name: "Settings" }).click();
    await expect(page.getByLabel("Name", { exact: true })).toBeVisible();
    const machine = await page.getByRole("region", { name: "Machine" }).boundingBox();
    const save = page.locator("form").getByRole("button", { name: "Save changes", exact: true });
    expect(machine).not.toBeNull();
    expect(machine!.width).toBeGreaterThanOrEqual(260);
    expect(machine!.width).toBeLessThanOrEqual(320);
    await expect(save).toBeInViewport();
    const printerName = page.getByLabel("Name");
    await printerName.fill("Workshop printer");
    await expect(page.getByText("Unsaved changes", { exact: true })).toBeVisible();
    await save.click();
    await expect(page.getByRole("heading", { name: "Workshop printer" })).toBeVisible();
    await expect(page).toHaveURL(/\/printers\/3\?tab=settings$/);
    await page.reload();
    await expect(page.getByRole("heading", { name: "Workshop printer" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Settings" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await page.getByRole("tab", { name: "Files" }).click();
    await expect(page.getByRole("complementary", { name: "Selected file" })).toContainText(
      "skadis_kitchen-roll_screw_PLA_30m12s.gcode",
    );
    await expect(page.getByText("Failed to fetch")).toHaveCount(0);
    await expect(page.getByText("This page could not be found")).toHaveCount(0);
    await expect(page).toHaveURL(/\/printers\/3\?tab=files$/);
    await page.goBack();
    await expect(page.getByRole("tab", { name: "Settings" })).toHaveAttribute(
      "aria-selected",
      "true",
    );

    const html = await page.content();
    expect(html).not.toContain('printerId":"$NaN');
    expect(problems).toEqual([]);
  });

  test("an operator can resolve a stale active job from the queue", async ({ page }) => {
    const problems = await collectPageProblems(page);

    await page.goto("/printers");
    await page.getByRole("tab", { name: "Queue" }).click();
    await page.getByRole("button", { name: "Active", exact: true }).click();
    await expect(
      page.getByRole("region", { name: "Active print jobs" }).getByText("interrupted-part.gcode"),
    ).toBeVisible();
    await page.getByRole("button", { name: "Resolve" }).click();

    const dialog = page.getByRole("dialog", { name: "Resolve stale job" });
    await expect(dialog).toContainText("does not send a command to the printer");
    await dialog.getByRole("button", { name: "Mark failed" }).click();

    await page.getByRole("button", { name: "Recent", exact: true }).click();
    await expect(page.getByRole("region", { name: "Recent print jobs" })).toBeVisible();
    await expect(
      page.getByRole("region", { name: "Recent print jobs" }).getByText(/ · failed$/),
    ).toBeVisible();
    expect(problems).toEqual([]);
  });
});

test.describe("printer workbench", () => {
  test("keeps the active print dashboard visible while sending commands", async ({ page }) => {
    const problems = await collectPageProblems(page);
    const connected = Promise.withResolvers<WebSocketRoute>();
    await page.routeWebSocket(/\/api\/v1\/printers\/3\/ws\?/, (socket) => {
      connected.resolve(socket);
    });

    const heatRequest = Promise.withResolvers<{ heater: string; target: number }>();
    const heatCommands: { heater: string; target: number }[] = [];
    const acceptHeat = Promise.withResolvers<void>();
    await page.route("**/api/v1/printers/3/temperature", async (route) => {
      const command = route.request().postDataJSON();
      heatCommands.push(command);
      heatRequest.resolve(command);
      await acceptHeat.promise;
      await route.fulfill({ json: { ok: true } });
    });
    for (const action of ["tune", "pause", "resume"]) {
      await page.route(`**/api/v1/printers/3/${action}`, (route) =>
        route.fulfill({ json: { ok: true } }),
      );
    }

    await page.goto("/printers/3");
    const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
    if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    const socket = await connected.promise;
    const update = (data: PrinterSnapshot, type = "update") =>
      socket.send(JSON.stringify({ type, data }));
    update(
      {
        webhooks: { state: "ready" },
        print_stats: {
          state: "printing",
          filename: "bracket.gcode",
          print_duration: 120,
          total_duration: 137,
        },
        extruder: { temperature: 204, target: 210 },
        heater_bed: { temperature: 59, target: 60 },
        virtual_sdcard: { progress: 0.42 },
        gcode_move: { speed_factor: 1, extrude_factor: 1 },
        fan: { speed: 0.6 },
      },
      "snapshot",
    );

    const job = page.getByRole("region", { name: "Current job" });
    const thermal = page.getByRole("region", { name: "Temperatures" });
    const graph = thermal.getByRole("figure", { name: "Heater history" });
    const tuning = page.getByRole("region", { name: "Print tuning" });
    await expect(job.getByText("bracket.gcode", { exact: true })).toBeInViewport();
    await expect(job.getByText("Elapsed", { exact: true })).toBeInViewport();
    await expect(job.getByText("2m 17s", { exact: true })).toBeInViewport();
    await expect(job.getByText("Print time", { exact: true })).toBeInViewport();
    await expect(job.getByText("2m 0s", { exact: true })).toBeInViewport();
    await expect(graph).toBeInViewport();
    await expect(graph).toContainText("204 °C");
    await expect(
      tuning.getByRole("spinbutton", { name: "Print speed target (%)" }),
    ).toBeInViewport();
    await expect(page.getByRole("region", { name: "Toolhead" })).toBeInViewport();
    await expect(page.getByRole("tab", { name: "Controls", exact: true })).toHaveCount(0);

    const target = thermal.getByRole("spinbutton", { name: "Hotend target" });
    await target.fill("215");
    update({ extruder: { temperature: 207, target: 211 } });
    await expect(thermal.getByText("Target 211 °C")).toBeVisible();
    await expect(target).toHaveValue("215");
    await expect(target).toBeFocused();

    try {
      await target.press("Enter");
      await expect(thermal.getByText("Sending command…")).toBeVisible();
      expect(await heatRequest.promise).toEqual({ heater: "extruder", target: 215 });
      await expect(job.getByText("bracket.gcode", { exact: true })).toBeInViewport();
      await expect(graph).toBeInViewport();
      await expect(page).toHaveURL(/\/printers\/3$/);
    } finally {
      acceptHeat.resolve();
    }
    await expect(thermal.getByText("Target command sent for 215 °C")).toBeVisible();
    await expect(thermal.getByText("Target 211 °C")).toBeVisible();
    await expect(target).toBeFocused();
    update({ extruder: { temperature: 209, target: 215 }, heater_bed: { temperature: 60 } });
    await expect(thermal.getByText("Target 215 °C", { exact: true })).toBeVisible();

    await tuning.getByRole("spinbutton", { name: "Print speed target (%)" }).fill("115");
    await tuning.getByRole("button", { name: "Set Print speed" }).click();
    await expect(tuning.getByText("Tuning command sent")).toBeVisible();
    await expect(tuning.getByText("100%", { exact: true })).toHaveCount(2);
    update({ gcode_move: { speed_factor: 1.15 } });
    await expect(tuning.getByText("115%", { exact: true })).toBeVisible();

    await job.getByRole("button", { name: "Pause", exact: true }).click();
    await expect(job.getByText("Pause command sent")).toBeVisible();
    await expect(job.getByRole("button", { name: "Resume", exact: true })).toHaveCount(0);
    update({ print_stats: { state: "paused" } });
    await job.getByRole("button", { name: "Resume", exact: true }).click();
    await expect(job.getByText("Resume command sent")).toBeVisible();
    update({ print_stats: { state: "printing" } });
    await expect(job.getByRole("button", { name: "Pause", exact: true })).toBeEnabled();
    await expect(graph).toBeInViewport();
    await expect(page).toHaveURL(/\/printers\/3$/);
    expect(problems).toEqual([]);

    await page.screenshot({ path: test.info().outputPath("klipper-workbench.png") });
    update({ print_stats: { state: "standby" } });
    const presets = thermal.getByRole("group", { name: "Material preset" });
    const petg = presets.getByRole("radio", { name: "PETG 240 / 80 °C" });
    await expect(petg).toBeEnabled();
    await petg.click();
    await expect(petg).toBeChecked();
    expect(heatCommands).toEqual([{ heater: "extruder", target: 215 }]);
    await thermal.getByRole("button", { name: "Preheat" }).click();
    await expect
      .poll(() => heatCommands)
      .toEqual([
        { heater: "extruder", target: 215 },
        { heater: "extruder", target: 240 },
        { heater: "bed", target: 80 },
      ]);
    await page.screenshot({ path: test.info().outputPath("klipper-presets.png") });
  });

  test("resolves a legacy Controls bookmark to the Bambu dashboard", async ({ page }) => {
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    const problems = await collectPageProblems(page);
    await page.addInitScript(() =>
      localStorage.setItem("printstash.printer-card.show-image", "true"),
    );
    const sidebar = page.getByRole("button", { name: "Toggle sidebar" });
    await page.route("**/api/v1/printers/3", async (route) => {
      const response = await route.fetch();
      const printer = await response.json();
      await route.fulfill({
        response,
        json: {
          ...printer,
          provider: "bambu_lan",
          model_name: "Bambu Lab P1S",
          capabilities: {
            ...printer.capabilities,
            can_pause: true,
            can_resume: true,
            can_cancel: true,
            can_send_gcode: false,
            can_report_material_state: true,
          },
        },
      });
    });
    await page.route("**/api/v1/printers/3/material-state", (route) =>
      route.fulfill({
        json: {
          tools: [],
          slots: [
            {
              slot_key: "ams:0:0",
              source: "bambu_ams",
              state: "loaded",
              material_type: "PETG",
              color_hex: "#4169E1",
              stale: false,
            },
            ...["loaded", "empty", "unknown"].map((state, index) => ({
              slot_key: `ams:0:${index + 1}`,
              source: "bambu_ams",
              state,
              material_type: state === "loaded" ? "PLA" : null,
              color_hex: state === "loaded" ? "#F2C166" : null,
              stale: false,
            })),
          ],
        },
      }),
    );
    const connected = Promise.withResolvers<WebSocketRoute>();
    await page.routeWebSocket(/\/api\/v1\/printers\/3\/ws\?/, (socket) => {
      connected.resolve(socket);
    });

    await page.goto("/printers/3?tab=controls");
    if ((await sidebar.getAttribute("aria-expanded")) === "true") await sidebar.click();
    await expect(sidebar).toHaveAttribute("aria-expanded", "false");
    const socket = await connected.promise;
    socket.send(
      JSON.stringify({
        type: "snapshot",
        data: {
          print_stats: { state: "standby" },
          extruder: { temperature: 22, target: 0 },
          heater_bed: { temperature: 22, target: 0 },
          fan: { speed: 0 },
        },
      }),
    );
    const job = page.getByRole("region", { name: "Current job" });
    const machine = page.getByRole("region", { name: "Machine" });
    const portrait = machine.locator("img");
    const idle = job.getByText("No active print");
    const queue = job.getByRole("button", { name: "Add to queue", exact: true });
    const history = page.getByRole("region", { name: "Print history" });
    const thermal = page.getByRole("region", { name: "Temperatures" });
    await expect(idle).toBeInViewport();
    await expect(queue).toBeInViewport();
    await expect(portrait).toBeInViewport();
    await expect(
      history.getByText("skadis_kitchen-roll_screw", { exact: true }).first(),
    ).toBeInViewport();
    await expect(history.getByText("Failed", { exact: true })).toBeVisible();
    const fan = thermal.getByRole("region", { name: "Part cooling fan" });
    const graph = thermal.getByRole("figure", { name: "Heater history" });
    const hotendPlot = graph.getByRole("group", { name: "Hotend" });
    const bedPlot = graph.getByRole("group", { name: "Bed" });
    await expect(fan).toContainText("0%");
    const rotor = fan.locator("svg");
    await expect(rotor).toBeVisible();
    await expect(rotor).toHaveCSS("transition-property", "none");
    await expect(rotor).toHaveCSS("transform", "matrix(1, 0, 0, 1, 0, 0)");
    await expect(thermal.getByText("22 °C", { exact: true })).toHaveCount(2);
    await expect(graph.locator("circle, polyline")).toHaveCount(0);
    const hotendBox = (await hotendPlot.boundingBox())!;
    const bedBox = (await bedPlot.boundingBox())!;
    expect(bedBox.y).toBeGreaterThan(hotendBox.y + hotendBox.height);
    expect(bedBox.x).toBe(hotendBox.x);
    expect(bedBox.width).toBe(hotendBox.width);
    for (const [plot, box] of [
      [hotendPlot, hotendBox],
      [bedPlot, bedBox],
    ] as const) {
      const ticks = plot.locator("span").filter({ hasText: /^(40|20|0) °C$/ });
      await expect(ticks).toHaveCount(3);
      for (const tick of await ticks.all()) {
        const bounds = (await tick.boundingBox())!;
        expect(bounds.y).toBeGreaterThanOrEqual(box.y);
        expect(bounds.y + bounds.height).toBeLessThanOrEqual(box.y + box.height);
      }
    }
    socket.send(
      JSON.stringify({
        type: "update",
        data: { extruder: { temperature: 22 }, heater_bed: { temperature: 22 } },
      }),
    );
    await expect(hotendPlot.locator("polyline")).toHaveCount(1);
    await expect(bedPlot.locator("polyline")).toHaveCount(1);
    await expect(graph.locator("circle, [stroke-dasharray]")).toHaveCount(0);
    await expect(page.getByRole("tab", { name: "Config", exact: true })).toHaveCount(0);
    const machineBox = (await machine.boundingBox())!;
    const jobBox = (await job.boundingBox())!;
    const idleBox = (await idle.boundingBox())!;
    const queueBox = (await queue.boundingBox())!;
    expect(machineBox.y).toBe(jobBox.y);
    expect(machineBox.x + machineBox.width).toBeLessThanOrEqual(jobBox.x);
    expect((await portrait.boundingBox())!.height).toBeGreaterThan(160);
    expect(queueBox.x - (idleBox.x + idleBox.width)).toBeLessThan(32);
    await expect(page.getByRole("region", { name: "AMS 0" }).getByRole("listitem")).toHaveCount(4);
    await expect(page.getByRole("region", { name: "AMS 0" }).getByText("PETG")).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath("bambu-idle.png") });
    socket.send(
      JSON.stringify({
        type: "update",
        data: {
          print_stats: { state: "printing", filename: "bambu-part.gcode" },
          extruder: { temperature: 220, target: 220 },
          heater_bed: { temperature: 60, target: 60 },
          fan: { speed: 0.5 },
        },
      }),
    );
    await expect(page.getByRole("tab", { name: "Status", exact: true })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await expect(page.getByRole("region", { name: "AMS 0" }).getByText("PETG")).toBeInViewport();
    await expect(page.getByRole("button", { name: "Pause", exact: true })).toBeEnabled();
    await expect(job.getByText("bambu-part.gcode", { exact: true })).toBeVisible();
    await expect(job.getByText("Elapsed", { exact: true })).toHaveCount(0);
    await expect(job.getByText("Print time", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("figure", { name: "Heater history" })).toContainText("220 °C");
    await expect(fan).toContainText("50%");
    await expect(rotor).toHaveCSS(
      "transform",
      "matrix(-0.707107, 0.707107, -0.707107, -0.707107, 0, 0)",
    );
    for (const panel of ["Toolhead", "Print tuning", "Bed surface"]) {
      await expect(page.getByRole("region", { name: panel })).toHaveCount(0);
    }
    await expect(page.getByRole("spinbutton", { name: "Hotend target" })).toHaveCount(0);
    await expect(page).toHaveURL(/\/printers\/3\?tab=controls$/);
    await page.screenshot({ path: test.info().outputPath("bambu-workbench-dark.png") });
    await page.emulateMedia({ reducedMotion: "no-preference" });
    await expect(rotor).toHaveCSS("transition-property", /^transform(?:,|$)/);
    await history.getByRole("link", { name: "Jobs", exact: true }).click();
    await expect(page).toHaveURL(/\?tab=jobs$/);
    await expect(page.getByRole("table", { name: "Print history" })).toContainText(
      "skadis_kitchen-roll_screw",
    );
    expect(problems).toEqual([]);
  });
});
