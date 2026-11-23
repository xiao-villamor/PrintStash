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

import { collectPageProblems, useMockApi } from "./_setup";

useMockApi();

test.describe("printer routes", () => {
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
    await card.getByRole("button", { name: "Settings", exact: true }).click();
    const settings = page.getByRole("dialog", { name: "Edit printer connection" });
    await expect(settings).toBeVisible();
    await expect(page).toHaveURL(/\/printers$/);
    await page.keyboard.press("Escape");
    await expect(settings).toBeHidden();

    await card.getByRole("button", { name: /set model|creality/i }).click();
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
    await page
      .getByRole("combobox", { name: "View", exact: true })
      .selectOption({ label: "Active" });
    await expect(page.getByText("interrupted-part.gcode")).toBeVisible();
    await page.getByRole("button", { name: "Resolve" }).click();

    const dialog = page.getByRole("dialog", { name: "Resolve stale job" });
    await expect(dialog).toContainText("does not send a command to the printer");
    await dialog.getByRole("button", { name: "Mark failed" }).click();

    await page
      .getByRole("combobox", { name: "View", exact: true })
      .selectOption({ label: "Recent" });
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
