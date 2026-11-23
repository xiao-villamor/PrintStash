/**
 * Inspect an existing Model using its real Artifacts, metadata and printer state.
 * This opt-in review opens controls without submitting edits, shares or prints.
 * Galleries are evidence for a later design audit, not a claim that the UI is correct.
 */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { modelReviewTest as test, expect } from "../helpers";
import { collapseNavigationSidebar, detailReviewRecorder, reviewArchiveSettings } from "../util";
import type { FilePlates, MetadataRead, ModelRead } from "../../../src/types";

test.describe("Existing Model review", () => {
  for (const theme of ["light", "dark"] as const) {
    for (const phase of ["details", "preview"] as const) {
      test(`inspects real Model ${phase} in ${theme}`, async ({ page }, info) => {
        page.setDefaultTimeout(15_000);
        await page.addInitScript((value) => localStorage.setItem("printstash.theme", value), theme);
        const target = new URL(process.env.PRINTSTASH_REVIEW_URL!);
        const modelId = Number(target.pathname.split("/").pop());
        const path = `/api/v1/models/${modelId}`;
        const read = await page.request.get(path);
        expect(read.ok()).toBe(true);
        const model: ModelRead = await read.json();
        const source = model.files.find((file) => file.file_type === "3mf");
        const revisions = model.files.filter((file) => file.file_type === "gcode");
        const sliced = revisions.find((file) => file.original_filename.endsWith(".gcode.3mf"));
        expect(source, "Review needs a real source 3MF").toBeDefined();
        expect(sliced, "Review needs a real sliced-3MF Revision").toBeDefined();
        const sourcePlates: FilePlates = await (
          await page.request.get(`/api/v1/files/${source!.id}/plates`)
        ).json();
        const slicedPlates: {
          plates: number[];
          identities: { number: number; name: string }[];
          plate_metadata: { number: number; metadata: MetadataRead }[];
        } = await (await page.request.get(`/api/v1/files/${sliced!.id}/gcode-plates`)).json();
        expect(sourcePlates.plates.length).toBeGreaterThan(1);
        expect(slicedPlates.plates.length).toBeGreaterThan(1);
        const review = detailReviewRecorder(page, info);
        let completed = false;
        const inspector = page.getByTestId("model-detail-sidebar").getByRole("tabpanel");
        const capturePanel = async (state: string) => {
          await expect(
            page
              .getByRole("region", { name: "Preview", exact: true })
              .getByRole("button", { name: "Screenshot", exact: true }),
          ).toBeEnabled({ timeout: 60_000 });
          await inspector.evaluate((el) => {
            el.scrollTop = 0;
          });
          await review.capture(state);
          let previous = 0;
          let step = 0;
          while (
            await inspector.evaluate((el) => el.scrollTop + el.clientHeight < el.scrollHeight - 1)
          ) {
            const top = await inspector.evaluate((el) => {
              el.scrollTop += el.clientHeight * 0.8;
              return el.scrollTop;
            });
            expect(top).toBeGreaterThan(previous);
            await review.capture(`${state}-scroll-${++step}`);
            previous = top;
          }
          await inspector.evaluate((el) => {
            el.scrollTop = 0;
          });
        };
        const tab = async (label: string) => {
          await page.getByRole("tab", { name: new RegExp(`^${label}\\b`) }).click();
          await expect(inspector).toHaveAttribute("aria-label", label);
        };
        const dismiss = async () => {
          await page.getByRole("dialog").press("Escape");
          await expect(page.getByRole("dialog")).toHaveCount(0);
        };
        try {
          await review.save("metadata.json", {
            origin: target.origin,
            modelId,
            files: model.files,
            sourcePlates,
            slicedPlates,
          });
          await page.goto(`${target.pathname}?tab=overview`);
          await collapseNavigationSidebar(page);
          const preview = page.getByRole("region", { name: "Preview", exact: true });
          const screenshot = preview.getByRole("button", { name: "Screenshot", exact: true });
          const waitForPreview = async () => {
            await expect(async () => {
              const retry = preview.getByRole("button", { name: "Try preview again", exact: true });
              if (await retry.isVisible()) await retry.click();
              await expect(screenshot).toBeEnabled({ timeout: 2_000 });
            }).toPass({ timeout: 60_000, intervals: [1_000, 2_000] });
          };
          await waitForPreview();

          if (phase === "details") {
            const canvas = preview.locator("canvas").first();
            const initialCanvas = await canvas.boundingBox();
            const plateTray = preview.getByRole("group", { name: "Plates", exact: true });
            // ── Every detail tab, retaining the data and empty states actually present ──
            for (const label of [
              "Overview",
              "Source info",
              "Print settings",
              "Revisions",
              "Files",
              "History",
              "Similar",
            ]) {
              await tab(label);
              expect(await canvas.boundingBox()).toEqual(initialCanvas);
              const frame = await preview.boundingBox();
              const tray = await plateTray.boundingBox();
              expect(tray!.y + tray!.height).toBeLessThanOrEqual(frame!.y + frame!.height);
              await capturePanel(label.toLowerCase().replaceAll(" ", "-"));
              if (label === "Overview") {
                const sections = [
                  inspector.getByRole("heading", { name: "Print setup" }),
                  inspector.getByRole("table", { name: "Plate estimates" }),
                  inspector.getByRole("button", { name: "Download", exact: true }),
                  inspector.getByRole("heading", { name: "Recorded prints" }),
                  inspector.locator("summary").filter({ hasText: "G-code file" }),
                ];
                let previousBottom = Number.NEGATIVE_INFINITY;
                for (const section of sections) {
                  const bounds = await section.boundingBox();
                  expect(bounds).toBeTruthy();
                  expect(bounds!.y).toBeGreaterThanOrEqual(previousBottom);
                  previousBottom = bounds!.y + bounds!.height;
                }
                const disclosure = sections[sections.length - 1];
                const fileSize = inspector.getByText("File size", { exact: true });
                await expect(fileSize).toBeHidden();
                await disclosure.focus();
                await disclosure.press("Enter");
                await expect(fileSize).toBeVisible();
                await capturePanel("overview-file-details");
                await disclosure.press("Enter");
                await expect(fileSize).toBeHidden();
              }
            }
            await inspector.getByRole("button", { name: "Filters", exact: true }).click();
            await review.capture("similarity-filters");
            await dismiss();
            await tab("Files");
            const sourceRow = inspector.getByRole("article", {
              name: source!.original_filename,
              exact: true,
            });
            await sourceRow
              .getByRole("button", {
                name: `Actions for ${source!.original_filename}`,
                exact: true,
              })
              .click();
            await review.capture("source-file-actions-menu");
            await page.keyboard.press("Escape");
            await expect(page.getByRole("menu")).toHaveCount(0);
            await sourceRow.getByRole("button", { name: /^(Add|Edit) tags$/ }).click();
            await expect(
              page.getByRole("dialog").getByPlaceholder("Search or create a tag", { exact: true }),
            ).toBeVisible();
            await review.capture("source-file-tags-editor");
            await dismiss();
            await tab("Revisions");
            const revisionButton = inspector.getByRole("button", {
              name: new RegExp(`^Rev ${sliced!.gcode_revision_number}\\b`),
            });
            if (revisions.length > 1) await revisionButton.click();
            const revisionTab = page.getByRole("tab", { name: /^Revisions/ });
            await revisionTab.focus();
            await page.keyboard.press("Tab");
            await expect(inspector).toBeFocused();
            const revisionEstimates = inspector.getByRole("region", { name: "Plate estimates" });
            if (slicedPlates.plates.length > 1) {
              await expect(revisionEstimates).toBeVisible();
              for (const plate of slicedPlates.identities) {
                await expect(
                  revisionEstimates
                    .getByRole("region", { name: `Plate ${String(plate.number).padStart(2, "0")}` })
                    .getByText(plate.name, { exact: true }),
                ).toBeVisible();
              }
            }
            await inspector.getByRole("button", { name: "More", exact: true }).click();
            await expect(page.getByRole("menuitem", { name: "Move to trash" })).toBeVisible();
            await review.capture("revision-actions");
            await page.keyboard.press("Escape");
            await inspector.getByRole("button", { name: "Preview G-code" }).click();
            await waitForPreview();
            await expect(
              preview.getByRole("button", { name: "G-code", exact: true }),
            ).toHaveAttribute("aria-pressed", "true");
            await review.capture("revision-toolpath");
            await preview.getByRole("button", { name: "3D model", exact: true }).click();
            await waitForPreview();
            const [download] = await Promise.all([
              page.waitForEvent("download"),
              inspector.getByRole("button", { name: "Download", exact: true }).click(),
            ]);
            const downloadedPath = (await download.path())!;
            expect(download.suggestedFilename()).toBe(sliced!.original_filename);
            expect(
              createHash("sha256")
                .update(await readFile(downloadedPath))
                .digest("hex"),
            ).toBe(sliced!.sha256);
            const embeddedSettings = await reviewArchiveSettings(downloadedPath);
            await review.save("metadata.json", {
              origin: target.origin,
              modelId,
              files: model.files,
              sourcePlates,
              slicedPlates,
              embeddedSettings,
            });
            // Compare persisted values to the downloaded original bytes, per plate.
            // The test does not manufacture estimates or assume one plate represents the archive.
            expect(slicedPlates.plate_metadata).toHaveLength(slicedPlates.plates.length);
            for (const plate of slicedPlates.plate_metadata) {
              const headers = embeddedSettings[`Metadata/plate_${plate.number}.gcode`];
              expect(headers).toBeDefined();
              const header = headers.join("\n");
              const value = (pattern: RegExp) => {
                const match = header.match(pattern);
                expect(match).not.toBeNull();
                return match![1].trim();
              };
              expect(plate.metadata.printer_model).toBe(value(/; printer_model = ([^\n]+)/));
              expect(plate.metadata.material_type).toBe(value(/; filament_type = ([^\n]+)/));
              expect(plate.metadata.layer_height_mm).toBe(
                Number(value(/; layer_height = ([^\n]+)/)),
              );
              expect(plate.metadata.nozzle_diameter_mm).toBe(
                Number(value(/; nozzle_diameter = ([^\n]+)/)),
              );
              expect(plate.metadata.nozzle_temperature_c).toBe(
                Number(value(/; nozzle_temperature = ([^\n]+)/)),
              );
              expect(plate.metadata.infill_percent).toBe(
                Number(value(/; sparse_infill_density = ([^%\n]+)/)),
              );
              expect(plate.metadata.wall_loops).toBe(Number(value(/; wall_loops = ([^\n]+)/)));
              expect(plate.metadata.filament_weight_g).toBeCloseTo(
                Number(value(/; total filament weight \[g\] : ([^\n]+)/)),
                2,
              );
              const time = header.match(
                /total estimated time:\s*(?:(\d+)h\s*)?(?:(\d+)m\s*)?(?:(\d+)s)?/,
              );
              expect(time).not.toBeNull();
              expect(plate.metadata.estimated_time_s).toBe(
                Number(time![1] ?? 0) * 3600 + Number(time![2] ?? 0) * 60 + Number(time![3] ?? 0),
              );
            }
            await tab("Print settings");
            await expect(
              inspector.getByText("No print settings are available for this sliced revision."),
            ).toHaveCount(0);
            const estimates = inspector.getByRole("region", {
              name: "Plate estimates",
              exact: true,
            });
            await expect(estimates).toBeVisible();
            for (const plate of slicedPlates.plate_metadata) {
              const row = estimates.getByRole("region", {
                name: `Plate ${String(plate.number).padStart(2, "0")}`,
                exact: true,
              });
              const header = embeddedSettings[`Metadata/plate_${plate.number}.gcode`].join("\n");
              const time = header.match(/total estimated time:\s*((?:(?:\d+h|\d+m|\d+s)\s*)+)/);
              const weight = header.match(/; total filament weight \[g\] : ([^\n]+)/);
              expect(time).not.toBeNull();
              expect(weight).not.toBeNull();
              await expect(row.getByText(time![1].trim(), { exact: true })).toBeVisible();
              await expect(
                row.getByText(
                  `${Number(weight![1]).toLocaleString("en-US", { maximumFractionDigits: 2 })} g`,
                  { exact: true },
                ),
              ).toBeVisible();
            }
            const shared = slicedPlates.plate_metadata[0].metadata;
            await expect(inspector.getByText(shared.printer_model!, { exact: true })).toBeVisible();
            await expect(inspector.getByText(shared.material_type!, { exact: true })).toBeVisible();
            await capturePanel("print-settings-extracted-values");
            await tab("Revisions");
            await review.capture("revision-original-download");
            await inspector.getByRole("button", { name: "Edit revision", exact: true }).click();
            await review.capture("revision-editor");
            await dismiss();
            await inspector.getByRole("button", { name: "Add revision", exact: true }).click();
            await review.capture("add-revision-dialog");
            await dismiss();
            if (revisions.length > 1) {
              await inspector.getByRole("button", { name: "Edit labels", exact: true }).click();
              await review.capture("revision-label-selection");
              await inspector
                .getByRole("button", { name: "Cancel selection", exact: true })
                .click();
            }
            await inspector.getByRole("button", { name: "Compare", exact: true }).click();
            if (revisions.length === 1 && model.files.length > 1) {
              await expect(
                inspector.getByRole("heading", { name: "Compare files", exact: true }),
              ).toBeFocused();
            }
            for (const label of ["G-code revisions", "Source files", "All files"]) {
              await inspector
                .getByRole("group", { name: "Comparison type", exact: true })
                .getByRole("button", { name: label, exact: true })
                .click();
              if (
                label === "All files" &&
                revisions.length === 1 &&
                slicedPlates.plates.length > 1
              ) {
                const allFields = inspector.getByRole("checkbox", {
                  name: "Show all comparison fields",
                });
                await expect(allFields).not.toBeChecked();
                const blanks = inspector.getByText("Not recorded", { exact: true });
                const compactCount = await blanks.count();
                await allFields.check();
                expect(await blanks.count()).toBeGreaterThan(compactCount);
                await review.capture("compare-full-fields");
                await allFields.uncheck();
                for (const metric of ["Estimated time", "Filament weight"]) {
                  const estimates = inspector.getByRole("row", { name: new RegExp("^" + metric) });
                  for (const number of slicedPlates.plates) {
                    await expect(
                      estimates.getByText("Plate " + String(number).padStart(2, "0"), {
                        exact: true,
                      }),
                    ).toBeVisible();
                  }
                }
              }
              await capturePanel(`compare-${label.toLowerCase().replaceAll(" ", "-")}`);
            }
            await inspector.getByRole("button", { name: "Back to revisions", exact: true }).click();

            for (const [origin, triggerName, backName] of [
              ["Files", "Compare", "Back to files"],
              ["Overview", "Compare revisions", "Back to overview"],
            ] as const) {
              await tab(origin);
              const trigger = inspector.getByRole("button", { name: triggerName, exact: true });
              await trigger.click();
              await inspector.getByRole("button", { name: backName, exact: true }).click();
              await expect(trigger).toBeFocused();
              await review.capture("compare-return-" + origin.toLowerCase());
            }

            // ── Inspect dialogs without saving, uploading, sharing or starting prints ──
            await page.getByRole("button", { name: /^Source files \(\d+\)$/ }).click();
            await expect(inspector).toBeFocused();
            await tab("Overview");
            await page.getByRole("button", { name: /^G-code revisions \(\d+\)$/ }).click();
            await expect(inspector).toBeFocused();
            await page.getByRole("button", { name: "More model actions", exact: true }).click();
            await review.capture("model-actions-menu");
            await page.getByRole("menuitem", { name: "Edit details" }).click();
            await review.capture("model-details-editor");
            await dismiss();
            await expect(revisionTab).toHaveAttribute("aria-selected", "true");
            await tab("Overview");
            await page
              .getByRole("button", {
                name: `${model.tags.length ? "Edit" : "Add"} tags ${model.tags.length ? "for" : "to"} ${model.name}`,
                exact: true,
              })
              .click();
            await review.capture("model-tags-editor");
            await dismiss();
            await page.getByRole("button", { name: "Edit cover", exact: true }).click();
            await review.capture("cover-editor");
            await dismiss();
            await page.getByRole("button", { name: "Share", exact: true }).click();
            await review.capture("share-dialog");
            await dismiss();
            await tab("Revisions");
            await inspector.getByRole("button", { name: "Preview G-code" }).click();
            const slicedPicker = page.getByRole("group", { name: "Sliced plates", exact: true });
            await slicedPicker
              .getByRole("button", {
                name: new RegExp("^Plate " + String(slicedPlates.plates[1]).padStart(2, "0")),
              })
              .click();
            const queue = inspector.getByRole("button", { name: "Add to queue", exact: true });
            if (await page.getByText("No printers", { exact: true }).isVisible()) {
              await expect(
                inspector.getByRole("link", { name: "Manage printers" }),
              ).toHaveAttribute("href", "/printers");
              await expect(
                inspector.getByRole("button", { name: "Send to printer", exact: true }),
              ).toHaveCount(0);
            }
            const printerless = await page.getByText("No printers", { exact: true }).isVisible();
            const expectedEstimates = new Map<number, string[]>();
            for (const number of slicedPlates.plates.slice(0, 2)) {
              const plate = revisionEstimates.getByRole("region", {
                name: "Plate " + String(number).padStart(2, "0"),
              });
              expectedEstimates.set(
                number,
                await Promise.all(
                  ["Estimated time", "Filament weight"].map((field) =>
                    plate.getByText(field, { exact: true }).locator("..").locator("dd").innerText(),
                  ),
                ),
              );
            }
            await queue.click();
            const send = page.getByRole("dialog");
            await expect(send.getByLabel("Sliced plate")).toHaveValue(
              String(slicedPlates.plates[1]),
            );
            const copies = send.getByRole("spinbutton", { name: "Copies" });
            const submitQueue = send.getByRole("button", { name: "Add to queue", exact: true });
            const evidence = send.getByLabel("Queue details");
            await expect(evidence.getByText("Revision test status")).toBeVisible();
            if (sliced!.revision_status === "needs_test")
              await expect(evidence.getByText("Needs test", { exact: true })).toBeVisible();
            for (const number of slicedPlates.plates.slice(0, 2)) {
              await send.getByLabel("Sliced plate").selectOption(String(number));
              for (const value of expectedEstimates.get(number)!) {
                await expect(evidence.getByText(value, { exact: true }).first()).toBeVisible();
              }
              await review.capture("queue-estimates-plate-" + number);
            }
            await copies.fill("1.5");
            await expect(copies).toHaveAttribute("aria-invalid", "true");
            await expect(submitQueue).toBeDisabled();
            await review.capture("queue-invalid-copies");
            await copies.fill("2");
            await expect(submitQueue).toBeEnabled();
            if (printerless) {
              await send.getByRole("radio", { name: "Choose printer", exact: true }).check();
              const setup = send.getByRole("link", { name: "Manage printers" });
              await expect(submitQueue).toBeDisabled();
              await review.capture("queue-manual-no-printers");
              const [setupPage] = await Promise.all([page.waitForEvent("popup"), setup.click()]);
              await expect(setupPage).toHaveURL(/\/printers(?:$|\?)/);
              await setupPage.close();
              await page.bringToFront();
              await expect(copies).toHaveValue("2");
              await expect(send.getByLabel("Sliced plate")).toHaveValue(
                String(slicedPlates.plates[1]),
              );
            }
            await review.capture("queue-inspected-plate");
            await send.getByRole("radio", { name: "Send now", exact: true }).check();
            await expect(send.getByLabel("Sliced plate")).toHaveValue(
              String(slicedPlates.plates[1]),
            );
            await expect(
              send.getByRole("button", { name: "Send to printer", exact: true }),
            ).toBeDisabled();
            await review.capture("send-inspected-plate");
            await send
              .getByRole("combobox", { name: "Sliced plate", exact: true })
              .selectOption(String(slicedPlates.plates[1]));
            await review.capture("send-plate-selected");
            await send.getByRole("radio", { name: "Add to queue", exact: true }).check();
            await expect(send.getByRole("heading", { name: /^Queue Rev / })).toBeVisible();
            await review.capture("queue-plate-selected");
            await dismiss();
            await tab("History");
            await page.getByRole("button", { name: "Add record", exact: true }).click();
            await review.capture("history-record-editor");
            await dismiss();
            await page.getByRole("button", { name: "Import from printer", exact: true }).click();
            await review.capture("history-import-dialog");
            await dismiss();

            // ── Browser-only read failure; retry returns to the actual source data ──
            review.mark("source-injected-error");
            await page.route(`**${path}/provenance`, (route) =>
              route.fulfill({
                status: 503,
                contentType: "application/json",
                body: '{"detail":"Review injected source failure"}',
              }),
            );
            await page.reload();
            await collapseNavigationSidebar(page);
            await tab("Source info");
            await expect(
              inspector.getByRole("button", { name: "Retry", exact: true }),
            ).toBeVisible();
            await expect(screenshot).toBeEnabled({ timeout: 60_000 });
            await review.capture("source-injected-error");
            await page.unroute(`**${path}/provenance`);
            const recovered = page.waitForResponse(
              (response) =>
                new URL(response.url()).pathname === `${path}/provenance` && response.ok(),
            );
            await inspector.getByRole("button", { name: "Retry", exact: true }).click();
            await recovered;
            await expect(inspector.getByRole("button", { name: "Retry", exact: true })).toHaveCount(
              0,
            );
            await capturePanel("source-recovered");
          } else {
            // ── Source plates: capture before/after Fit instead of hiding framing faults ──
            await tab("Source info");
            await expect(
              page.getByRole("heading", { name: "Whole source geometry", exact: true }),
            ).toBeVisible();
            await review.capture("mesh-all-plates");
            const plates = page.getByRole("group", { name: "Plates", exact: true });
            const assertImageMatte = async (group: typeof plates) => {
              for (const plate of sourcePlates.plates) {
                expect(plate.name).toBeTruthy();
                const thumbnail = group
                  .getByRole("button", { name: plate.name! })
                  .locator("[data-library-thumbnail]");
                await expect(thumbnail).toHaveAttribute("data-library-thumbnail", "ready");
                const color = await thumbnail.evaluate(
                  (element) => getComputedStyle(element).backgroundColor,
                );
                const channels = color.match(/\d+/g)?.map(Number);
                expect(
                  channels?.slice(0, 3).every((channel) => channel >= 220),
                  `thumbnail matte must remain light: ${color}`,
                ).toBe(true);
              }
            };
            await assertImageMatte(plates);
            const assertDockAlignment = async () => {
              const frame = await preview.boundingBox();
              const dock = await preview.locator(".detail-viewer-dock").boundingBox();
              expect(frame).not.toBeNull();
              expect(dock).not.toBeNull();
              // The dock meets the preview's inner edge on both sides, including expanded mode.
              expect(Math.abs(dock!.x - frame!.x)).toBeLessThanOrEqual(2);
              expect(Math.abs(dock!.x + dock!.width - frame!.x - frame!.width)).toBeLessThanOrEqual(
                2,
              );
              const workspace = preview.locator(".toolpath-workspace");
              if (await workspace.count()) {
                const box = await workspace.boundingBox();
                expect(box).not.toBeNull();
                expect(box!.y + box!.height).toBeLessThanOrEqual(dock!.y + 1);
              }
            };
            await assertDockAlignment();
            for (const plate of sourcePlates.plates) {
              await plates
                .getByRole("button", {
                  name: new RegExp(`^Plate ${String(plate.number).padStart(2, "0")}\\b`),
                })
                .click();
              await expect(preview.getByText(/^Selected plate mesh bounds:/)).toBeVisible();
              await review.capture(`mesh-plate-${plate.number}`);
              await preview.getByRole("button", { name: "Expand", exact: true }).click();
              await assertDockAlignment();
              await review.capture(`mesh-plate-${plate.number}-expanded`);
              await preview.getByRole("button", { name: "Collapse preview", exact: true }).click();
              await preview.getByRole("button", { name: "Fit to view", exact: true }).click();
            }
            const display = preview.getByRole("combobox", { name: "Display" });
            for (const mode of ["xray", "wireframe", "solid"]) {
              await display.selectOption(mode);
              await expect(display).toHaveValue(mode);
            }
            for (const camera of ["Top", "Front", "Isometric"]) {
              await preview.getByRole("button", { name: camera, exact: true }).click();
            }
            await preview.getByRole("button", { name: "Expand", exact: true }).click();
            await assertDockAlignment();
            const expandedSourcePlateBox = await plates.boundingBox();
            await review.capture("mesh-expanded");
            await page.keyboard.press("Escape");
            await expect(
              preview.getByRole("button", { name: "Expand", exact: true }),
            ).toBeFocused();
            await expect(page.getByTestId("model-detail-sidebar")).toBeVisible();
            await waitForPreview();
            // Shared overlays and camera presets retain their state and location across modes.
            const grid = preview.getByRole("button", { name: "Build plate grid", exact: true });
            const dimensions = preview.getByRole("button", { name: "Dimensions", exact: true });
            await grid.click();
            await dimensions.click();
            const gridState = await grid.getAttribute("aria-pressed");
            const dimensionsState = await dimensions.getAttribute("aria-pressed");
            const gridBox = await grid.boundingBox();
            const sourcePlateBox = await plates.boundingBox();
            await preview.getByRole("button", { name: "G-code", exact: true }).click();
            await expect(grid).toHaveAttribute("aria-pressed", gridState!);
            await expect(dimensions).toHaveAttribute("aria-pressed", dimensionsState!);
            const toolpathGridBox = await grid.boundingBox();
            expect(gridBox).not.toBeNull();
            expect(toolpathGridBox).not.toBeNull();
            expect(Math.abs(gridBox!.x - toolpathGridBox!.x)).toBeLessThan(2);
            expect(Math.abs(gridBox!.y - toolpathGridBox!.y)).toBeLessThan(2);
            for (const action of [
              "Isometric",
              "Front",
              "Top",
              "Zoom in",
              "Zoom out",
              "Fit to view",
            ]) {
              await expect(
                preview.getByRole("button", { name: action, exact: true }),
              ).toBeVisible();
            }
            const controls = page.getByRole("complementary", { name: "Toolpath controls" });
            const filePicker = controls.getByRole("combobox", { name: "G-code file" });
            if (await filePicker.count()) await filePicker.selectOption(String(sliced!.id));
            const slicedGroup = page.getByRole("group", { name: "Sliced plates", exact: true });
            const assertPlatePlacement = async (
              sourceBox: Awaited<ReturnType<typeof plates.boundingBox>>,
            ) => {
              const slicedBox = await slicedGroup.boundingBox();
              expect(sourceBox).not.toBeNull();
              expect(slicedBox).not.toBeNull();
              for (const key of ["x", "y", "width"] as const)
                expect(
                  Math.abs(sourceBox![key] - slicedBox![key]),
                  `plate picker ${key} should remain stable`,
                ).toBeLessThanOrEqual(2);
            };
            const assertPlateCardsFit = async () => {
              for (const sourcePlate of sourcePlates.plates) {
                expect(sourcePlate.name).toBeTruthy();
                const card = slicedGroup.getByRole("button", { name: sourcePlate.name! });
                await card.scrollIntoViewIfNeeded();
                const cardBox = await card.boundingBox();
                const nameBox = await card
                  .getByText(sourcePlate.name!, { exact: true })
                  .boundingBox();
                expect(cardBox).not.toBeNull();
                expect(nameBox).not.toBeNull();
                expect(nameBox!.x).toBeGreaterThanOrEqual(cardBox!.x);
                expect(nameBox!.y).toBeGreaterThanOrEqual(cardBox!.y);
                expect(nameBox!.x + nameBox!.width).toBeLessThanOrEqual(
                  cardBox!.x + cardBox!.width,
                );
                expect(nameBox!.y + nameBox!.height).toBeLessThanOrEqual(
                  cardBox!.y + cardBox!.height,
                );
              }
              const boxes = await slicedGroup.getByRole("button").evaluateAll((cards) =>
                cards.map((card) => {
                  const box = card.getBoundingClientRect();
                  return { top: box.top, bottom: box.bottom, left: box.left, right: box.right };
                }),
              );
              for (let i = 1; i < boxes.length; i++) {
                const previous = boxes[i - 1];
                const current = boxes[i];
                expect(current.left >= previous.right || current.top >= previous.bottom).toBe(true);
              }
            };
            const layer = controls.getByRole("spinbutton", { name: "Layer number" });
            await expect(layer).toBeVisible({ timeout: 60_000 });
            await waitForPreview();
            await assertPlatePlacement(sourcePlateBox);
            await assertDockAlignment();
            for (const action of [
              "Build plate grid",
              "Dimensions",
              "Isometric",
              "Front",
              "Top",
              "Zoom in",
              "Zoom out",
              "Fit to view",
            ]) {
              const control = preview.getByRole("button", { name: action, exact: true });
              expect(
                await control.evaluate((element) => {
                  const box = element.getBoundingClientRect();
                  const parent = element.closest('[role="region"][aria-label="Preview"]');
                  if (!parent) return false;
                  const bounds = parent.getBoundingClientRect();
                  const hit = document.elementFromPoint(
                    box.left + box.width / 2,
                    box.top + box.height / 2,
                  );
                  return (
                    box.left >= bounds.left &&
                    box.right <= bounds.right &&
                    box.top >= bounds.top &&
                    box.bottom <= bounds.bottom &&
                    hit !== null &&
                    element.contains(hit)
                  );
                }),
                `${action} must fit and be reachable in the preview`,
              ).toBe(true);
            }
            await assertPlateCardsFit();
            await assertImageMatte(slicedGroup);
            // Match by the actual plate name, not by the order of either list.
            // This review is for the supplied multi-plate artifacts with named plates.
            const selectedSource = sourcePlates.plates.at(-1)!;
            expect(selectedSource.name).toBeTruthy();
            const selectedSliced = slicedGroup.getByRole("button", {
              name: selectedSource.name!,
            });
            await expect(selectedSliced).toHaveAttribute("aria-pressed", "true", {
              timeout: 60_000,
            });
            await review.capture("toolpath-entry-selected-plate");
            for (const sourcePlate of sourcePlates.plates) {
              expect(sourcePlate.name).toBeTruthy();
              const slicedCard = slicedGroup.getByRole("button", { name: sourcePlate.name! });
              await expect(slicedCard).toHaveCount(1);
              await slicedCard.focus();
              await expect(slicedCard).toBeFocused();
              await slicedCard.press("Enter");
              await expect(slicedCard).toHaveAttribute("aria-pressed", "true");
              await waitForPreview();
              await preview.getByRole("button", { name: "3D model", exact: true }).click();
              const sourceCard = plates.getByRole("button", { name: sourcePlate.name! });
              await expect(sourceCard).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 });
              await waitForPreview();
              await preview.getByRole("button", { name: "G-code", exact: true }).click();
              await expect(slicedCard).toHaveAttribute("aria-pressed", "true", { timeout: 60_000 });
              await waitForPreview();
            }
            for (const plate of slicedPlates.plates) {
              await slicedGroup
                .getByRole("button", {
                  name: new RegExp(`^Plate ${String(plate).padStart(2, "0")}\\b`),
                })
                .click();
              await expect(layer).toBeVisible({ timeout: 60_000 });
              await waitForPreview();
              await review.capture(`toolpath-plate-${plate}-full`);
              const middle = String(
                Math.max(1, Math.floor(Number(await layer.getAttribute("max")) / 2)),
              );
              await layer.fill(middle);
              await layer.press("Enter");
              await expect(layer).toHaveValue(middle);
              await review.capture(`toolpath-plate-${plate}-middle`);
              await preview.getByRole("button", { name: "Expand", exact: true }).click();
              await assertDockAlignment();
              await expect(layer).toHaveValue(middle);
              await review.capture(`toolpath-plate-${plate}-expanded`);
              await preview.getByRole("button", { name: "Collapse preview", exact: true }).click();
            }
            await controls.getByRole("tab", { name: "View", exact: true }).click();
            if (sliced!.metadata?.printer_model === "Bambu Lab P1S") {
              await expect(
                controls.getByRole("button", { name: "Bed 256×256", exact: true }),
              ).toBeVisible();
            }
            if (dimensionsState === "false") await dimensions.click();
            await expect(
              controls.getByText("Extrusion bounds (includes any purge; excludes travel)"),
            ).toBeVisible();
            await review.capture("toolpath-view");
            if (dimensionsState === "false") await dimensions.click();
            for (const camera of ["Top", "Front", "Isometric"]) {
              const preset = preview.getByRole("button", { name: camera, exact: true });
              await preset.click();
              await expect(preset).toHaveAttribute("aria-pressed", "true");
            }
            await controls.getByRole("button", { name: "Travel", exact: true }).click();
            await expect(
              controls.getByRole("button", { name: "Travel", exact: true }),
            ).toHaveAttribute("aria-pressed", "true");
            for (const action of ["Zoom in", "Zoom out", "Fit to view"])
              await preview.getByRole("button", { name: action, exact: true }).click();
            await preview.getByRole("button", { name: "Expand", exact: true }).click();
            await assertDockAlignment();
            await assertPlatePlacement(expandedSourcePlateBox);
            await review.capture("toolpath-expanded-view");
            await controls.getByRole("tab", { name: "Layers", exact: true }).click();
            await assertPlateCardsFit();
            await review.capture("toolpath-expanded-layers");
            await preview.getByRole("button", { name: "Collapse preview", exact: true }).click();
            await preview.getByRole("button", { name: "3D model", exact: true }).click();
            await waitForPreview();
            await expect(grid).toHaveAttribute("aria-pressed", gridState!);
            await expect(dimensions).toHaveAttribute("aria-pressed", dimensionsState!);
            await review.capture("mesh-return-from-toolpath");
          }
          const after: ModelRead = await (await page.request.get(path)).json();
          expect(after.edit_version).toBe(model.edit_version);
          expect(after.edit_epoch).toBe(model.edit_epoch);
          expect(after.files).toEqual(model.files);
          completed = true;
        } finally {
          await review.finish(completed);
        }
      });
    }
  }
});
