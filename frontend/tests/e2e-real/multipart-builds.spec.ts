/**
 * A manufacturing run counts confirmed pieces and retains the previous print.
 * The real API cancels queued jobs to model interrupted attempts; all result
 * confirmation and replacement planning happens through browser controls.
 */
import { test, expect } from "./helpers";
import { modelCard, uploadGcodeModel } from "./util";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}/api/v1`;

test.describe("Multipart manufacturing", () => {
  test("three usable legs leave one replacement with both attempts retained", async ({ page }) => {
    const name = `Table legs ${Date.now()}`;
    await uploadGcodeModel(page, name);
    const href = await modelCard(page, name).getAttribute("href");
    const modelId = Number(href?.split("/").pop());
    const model = await (await page.request.get(`${API}/models/${modelId}`)).json();
    const revision = model.files.find((file: { file_type: string }) => file.file_type === "gcode");
    const created = await page.request.post(`${API}/multipart-models`, { data: { name } });
    expect(created.status()).toBe(201);
    const composition = await created.json();
    const saved = await page.request.put(`${API}/multipart-models/${composition.id}`, {
      data: { parts: [{ name: "Leg", quantity: 4, choices: [{ model_id: modelId }] }] },
    });
    expect(saved.ok()).toBeTruthy();
    await page.goto(`/builds?multipart=${composition.id}`);
    await expect(page.getByLabel("Build name")).toHaveValue(name);
    await page.getByRole("button", { name: "Create build" }).click();
    await expect(page).toHaveURL(/\/builds\/\d+$/);
    const buildId = Number(new URL(page.url()).pathname.split("/").pop());
    const part = page.getByRole("region", { name: "Leg", exact: true });
    await expect(part.getByText("4 missing", { exact: true })).toBeVisible();
    await part.getByLabel("Revision for the next jobs").selectOption(String(revision.id));
    await part.getByLabel("Pieces produced by this file").fill("4");
    await expect(part.getByLabel("Print jobs to queue")).toHaveValue("1");
    await part.getByRole("button", { name: "Queue pieces" }).click();
    await expect(part.getByRole("button", { name: "Queue pieces" })).toBeDisabled();
    const first = await (await page.request.get(`${API}/multipart-builds/${buildId}`)).json();
    const firstJob = first.parts[0].attempts[0].job_id;
    const cancelled = await page.request.delete(`${API}/fleet/queue/${firstJob}`);
    expect(cancelled.ok()).toBeTruthy();
    await page.getByRole("button", { name: "Refresh" }).click();
    const firstAttempt = part.getByRole("form", { name: `Job #${firstJob}` });
    await firstAttempt.getByLabel("Confirmed usable").fill("3");
    await firstAttempt.getByRole("button", { name: "Confirm result" }).click();
    await expect(part.getByText("1 missing", { exact: true })).toBeVisible();
    await part.getByLabel("Pieces produced by this file").fill("1");
    await expect(part.getByLabel("Print jobs to queue")).toHaveValue("1");
    await part.getByRole("button", { name: "Queue pieces" }).click();
    await expect(part.getByRole("form", { name: /Job #/ })).toHaveCount(2);
    const replacement = await (await page.request.get(`${API}/multipart-builds/${buildId}`)).json();
    const secondJob = replacement.parts[0].attempts[1].job_id;
    expect(secondJob).not.toBe(firstJob);
    expect((await page.request.delete(`${API}/fleet/queue/${secondJob}`)).ok()).toBeTruthy();
    await page.getByRole("button", { name: "Refresh" }).click();
    const secondAttempt = part.getByRole("form", { name: `Job #${secondJob}` });
    await secondAttempt.getByLabel("Confirmed usable").fill("1");
    await secondAttempt.getByRole("button", { name: "Confirm result" }).click();
    await expect(page.getByText("All required pieces confirmed", { exact: true })).toBeVisible();
    await expect(part.getByText("0 missing", { exact: true })).toBeVisible();
    await expect(firstAttempt.getByLabel("Confirmed usable")).toHaveValue("3");
    await expect(part.getByRole("form", { name: /Job #/ })).toHaveCount(2);
  });
});
