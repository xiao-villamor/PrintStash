/** Real migration preserves baseline and online delta Artifact bytes across API restart and cutover. */
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { test, expect } from "../helpers";
import { gcodeFor, uploadGcodeModel, modelCard } from "../util";

test.describe("Vault migration", () => {
  test("verified migration resumes after restart with online delta Artifacts", async ({ page }) => {
    test.setTimeout(240_000);
    const baselineName = `migration-baseline-${Date.now()}`;
    const deltaName = `migration-delta-${Date.now()}`;
    const dataRoot = process.env.PLAYWRIGHT_REAL_DATA_DIR;
    if (!dataRoot)
      throw new Error("Migration browser configuration must provide isolated data roots");
    const destination = join(dataRoot, baselineName);
    const data = join(destination, "files");
    const thumbs = join(destination, "thumbs");
    await Promise.all([mkdir(data, { recursive: true }), mkdir(thumbs, { recursive: true })]);

    // Persist a real baseline Artifact and its verified-backup prerequisite.
    await uploadGcodeModel(page, baselineName);
    const backupResponse = await page.request.post("/api/v1/backups");
    expect(backupResponse.ok()).toBeTruthy();
    const backup = await backupResponse.json();
    await page.goto("/settings?section=storage");
    const panel = page.getByRole("region", { name: "Move Vault storage" });
    await panel.getByLabel("Models directory", { exact: true }).fill(data);
    await panel.getByLabel("Thumbnail directory", { exact: true }).fill(thumbs);
    const backupOption = panel
      .getByLabel("Recent backup")
      .getByRole("option")
      .filter({ hasText: backup.backup_id });
    await panel
      .getByLabel("Recent backup")
      .selectOption((await backupOption.getAttribute("value")) ?? "");
    await panel.getByRole("button", { name: "Check migration plan" }).click();
    await expect(panel.getByText("Migration plan checked", { exact: true })).toBeVisible();
    const started = page.waitForResponse(
      (response) => response.url().endsWith("/start") && response.request().method() === "POST",
    );
    await panel.getByRole("button", { name: "Start verified copy" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Start verified copy" }).click();
    const run = await (await started).json();
    // The durable pause accepts copying or ready, so this remains deterministic
    // even when the tiny baseline finishes before the browser receives its reply.
    const paused = await page.request.post(`/api/v1/storage/migrations/${run.id}/pause`, {
      data: {},
    });
    expect(paused.ok()).toBeTruthy();

    // New ingestion remains source-authoritative before the final delta census.
    await uploadGcodeModel(page, deltaName);
    const listing = await (await page.request.get("/api/v1/models")).json();
    const models = listing.filter((item: { name: string }) =>
      [baselineName, deltaName].includes(item.name),
    );
    expect(models).toHaveLength(2);
    for (const model of models) {
      const detail = await (await page.request.get(`/api/v1/models/${model.id}`)).json();
      expect(detail.files).toHaveLength(1);
      const read = await page.request.get(`/api/v1/files/${detail.files[0].id}/download`);
      expect(read.ok()).toBeTruthy();
      expect(await read.body()).toEqual(Buffer.from(gcodeFor(model.name)));
    }

    // Restart the actual supervised API, then recover from persisted evidence.
    expect((await page.request.post("/api/v1/system/restart")).status()).toBe(202);
    const healthy = async () => {
      try {
        return (await page.request.get("/api/v1/health")).ok();
      } catch {
        return false;
      }
    };
    await expect.poll(healthy, { timeout: 30_000, intervals: [50, 100, 250] }).toBe(false);
    await expect.poll(healthy, { timeout: 60_000, intervals: [100, 250, 500] }).toBe(true);
    await page.goto("/settings?section=storage");
    await panel.getByRole("button", { name: "Recover migration" }).click();
    await panel.getByRole("button", { name: "Resume copy" }).click();
    await expect(panel.getByRole("button", { name: "Switch Vault storage" })).toBeVisible();
    await panel.getByRole("button", { name: "Switch Vault storage" }).click();
    const confirmation = page.getByRole("dialog");
    await expect(confirmation).toContainText("writes are paused");
    await confirmation.getByRole("button", { name: "Switch Vault storage" }).click();
    await expect(panel.getByText("Destination is active", { exact: true })).toBeVisible();
    await expect(panel.getByText(/will not be deleted automatically/)).toBeVisible();
    await expect(panel.getByRole("button", { name: "Clean up retained source" })).toBeDisabled();

    // Every baseline/delta Artifact must now download from the active destination.
    for (const model of models) {
      const detail = await (await page.request.get(`/api/v1/models/${model.id}`)).json();
      for (const artifact of detail.files) {
        const read = await page.request.get(`/api/v1/files/${artifact.id}/download`);
        expect(read.ok()).toBeTruthy();
        expect(await read.body()).toEqual(Buffer.from(gcodeFor(model.name)));
      }
    }
    await panel.getByRole("button", { name: "Run Full audit" }).click();
    await expect
      .poll(
        async () => {
          const response = await page.request.get(`/api/v1/storage/migrations/${run.id}`);
          return (await response.json()).full_audit;
        },
        { timeout: 60_000 },
      )
      .toMatchObject({ state: "completed", critical_count: 0 });
    const reportDownload = page.waitForEvent("download");
    await panel.getByRole("button", { name: "Download migration report" }).click();
    expect((await reportDownload).suggestedFilename()).toBe(`printstash-migration-${run.id}.json`);
    await page.goto("/");
    await expect(modelCard(page, baselineName)).toBeVisible();
    await expect(modelCard(page, deltaName)).toBeVisible();
    // Active roots remain inside this suite's disposable data directory. They
    // are reclaimed only when the next isolated backend launch resets that tree.
  });
});
