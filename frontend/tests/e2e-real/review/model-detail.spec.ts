/**
 * Inspect an existing Model using its real Artifacts, metadata and printer state.
 * This opt-in review opens controls without submitting edits, shares or prints.
 * Galleries are evidence for a later design audit, not a claim that the UI is correct.
 */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import { modelReviewTest as test, expect } from "../helpers";
import { collapseNavigationSidebar, detailReviewRecorder, reviewArchiveSettings } from "../util";
import type { FilePlates, ModelRead } from "../../../src/types";

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
        const slicedPlates: { plates: number[] } = await (
          await page.request.get(`/api/v1/files/${sliced!.id}/gcode-plates`)
        ).json();
        expect(sourcePlates.plates.length).toBeGreaterThan(1);
        expect(slicedPlates.plates.length).toBeGreaterThan(1);
        const review = detailReviewRecorder(page, info);
        let completed = false;
        const inspector = page.getByTestId("model-detail-sidebar").getByRole("tabpanel");
        const capturePanel = async (state: string) => {
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
          await expect(screenshot).toBeEnabled({ timeout: 60_000 });

          if (phase === "details") {
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
              await capturePanel(label.toLowerCase().replaceAll(" ", "-"));
            }
            await tab("Revisions");
            await inspector
              .getByRole("button", { name: new RegExp(`^Rev ${sliced!.gcode_revision_number}\\b`) })
              .click();
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
            await review.save("metadata.json", {
              origin: target.origin,
              modelId,
              files: model.files,
              sourcePlates,
              slicedPlates,
              embeddedSettings: await reviewArchiveSettings(downloadedPath),
            });
            await review.capture("revision-original-download");
            await inspector.getByRole("button", { name: "Edit revision", exact: true }).click();
            await review.capture("revision-editor");
            await dismiss();
            await inspector.getByRole("button", { name: "Add revision", exact: true }).click();
            await review.capture("add-revision-dialog");
            await dismiss();
            await inspector.getByRole("button", { name: "Edit labels", exact: true }).click();
            await review.capture("revision-label-selection");
            await inspector.getByRole("button", { name: "Cancel selection", exact: true }).click();
            await inspector.getByRole("button", { name: "Compare", exact: true }).click();
            for (const label of ["G-code revisions", "Source files", "All files"]) {
              await inspector
                .getByRole("group", { name: "Comparison type", exact: true })
                .getByRole("button", { name: label, exact: true })
                .click();
              await capturePanel(`compare-${label.toLowerCase().replaceAll(" ", "-")}`);
            }
            await inspector.getByRole("button", { name: "Back to revisions", exact: true }).click();

            // ── Inspect dialogs without saving, uploading, sharing or starting prints ──
            await tab("Overview");
            await page.getByRole("heading", { level: 1 }).click({ button: "right" });
            await review.capture("model-actions-menu");
            await page.getByRole("menuitem", { name: "Edit details" }).click();
            await review.capture("model-details-editor");
            await dismiss();
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
            await inspector.getByRole("button", { name: "Send to printer", exact: true }).click();
            const send = page.getByRole("dialog");
            await expect(
              send.getByRole("button", { name: "Send to printer", exact: true }),
            ).toBeDisabled();
            await review.capture("send-plate-required");
            await send
              .getByRole("combobox", { name: "Sliced plate", exact: true })
              .selectOption(String(slicedPlates.plates[1]));
            await review.capture("send-plate-selected");
            await send.getByRole("radio", { name: "Add to queue", exact: true }).check();
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
            await review.capture("mesh-all-plates");
            const plates = page.getByRole("group", { name: "Plates", exact: true });
            for (const plate of sourcePlates.plates) {
              await plates
                .getByRole("button", {
                  name: new RegExp(`^Plate ${String(plate.number).padStart(2, "0")}\\b`),
                })
                .click();
              await review.capture(`mesh-plate-${plate.number}`);
              await preview.getByRole("button", { name: "Fit to view", exact: true }).click();
              await review.capture(`mesh-plate-${plate.number}-fitted`);
            }
            for (const mode of ["X-Ray", "Wireframe", "Solid"]) {
              await preview.getByRole("button", { name: mode, exact: true }).click();
              await expect(
                preview.getByRole("button", { name: mode, exact: true }),
              ).toHaveAttribute("aria-pressed", "true");
              await review.capture(`mesh-${mode.toLowerCase()}`);
            }
            for (const camera of ["Top", "Front", "Isometric"]) {
              await preview.getByRole("button", { name: camera, exact: true }).click();
              await review.capture(`mesh-camera-${camera.toLowerCase()}`);
            }
            await preview.getByRole("button", { name: "Expand", exact: true }).click();
            await review.capture("mesh-expanded");
            await preview.getByRole("button", { name: "Collapse preview", exact: true }).click();
            await preview.getByRole("button", { name: "Layers", exact: true }).click();
            const controls = page.getByRole("complementary", { name: "Toolpath controls" });
            await controls
              .getByRole("combobox", { name: "G-code file" })
              .selectOption(String(sliced!.id));
            const layer = controls.getByRole("spinbutton", { name: "Layer number" });
            await expect(layer).toBeVisible({ timeout: 60_000 });
            await review.capture("toolpath-entry-selected-plate");
            for (const plate of slicedPlates.plates) {
              await controls
                .getByRole("button", {
                  name: `Plate ${String(plate).padStart(2, "0")}`,
                  exact: true,
                })
                .click();
              await expect(layer).toBeVisible({ timeout: 60_000 });
              await review.capture(`toolpath-plate-${plate}-full`);
              const middle = String(
                Math.max(1, Math.floor(Number(await layer.getAttribute("max")) / 2)),
              );
              await layer.fill(middle);
              await layer.press("Enter");
              await expect(layer).toHaveValue(middle);
              await review.capture(`toolpath-plate-${plate}-middle`);
            }
            await controls.getByRole("tab", { name: "View", exact: true }).click();
            await review.capture("toolpath-view");
            for (const camera of ["top", "front", "isometric"]) {
              await controls
                .getByRole("combobox", { name: "Camera view", exact: true })
                .selectOption(camera);
              await review.capture(`toolpath-camera-${camera}`);
            }
            await controls.getByRole("button", { name: "Travel", exact: true }).click();
            await expect(
              controls.getByRole("button", { name: "Travel", exact: true }),
            ).toHaveAttribute("aria-pressed", "true");
            for (const action of ["Zoom in", "Zoom out", "Fit to view"])
              await controls.getByRole("button", { name: action, exact: true }).click();
            await preview.getByRole("button", { name: "Expand", exact: true }).click();
            await review.capture("toolpath-expanded-view");
            await controls.getByRole("tab", { name: "Layers", exact: true }).click();
            await review.capture("toolpath-expanded-layers");
            await preview.getByRole("button", { name: "Collapse preview", exact: true }).click();
            await preview.getByRole("button", { name: "3D model", exact: true }).click();
            await expect(screenshot).toBeEnabled({ timeout: 60_000 });
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
