/** Captures recover original bytes, expose their profiles and keep geometry evidence advisory. */
import { randomUUID } from "node:crypto";
import { test, expect } from "./helpers";
import { collapseNavigationSidebar, stlFor } from "./util";
import { captureSequentialFiles } from "../../../browser-extension/sequential-capture";
import { claimBrowserPairing } from "../../../browser-extension/core";

// The provider returns controlled STL bytes; extension transport, persistence,
// pairing, recovery, review UI and ingestion all run against the real backend.
test.describe("inbox-capture", () => {
  test("imports the browser capture after transfer without opening review", async ({ page }) => {
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
    const publication = String(Date.now());
    const { code } = await (await page.request.post(`${api}/api/v1/browser-pairings`)).json();
    const device = await claimBrowserPairing({
      vault: api,
      code,
      name: `Quick capture ${publication}`,
    });
    const saved = new Map<string, unknown>();
    const candidate = { id: "bracket", filename: "bracket.stl", fileType: "stl" as const };
    const result = await captureSequentialFiles({
      vault: api,
      authorization: device.deviceCredential,
      source: {
        provider: "printables",
        canonical_url: `https://www.printables.com/model/${publication}-bracket`,
        source_item_id: publication,
        source_revision: null,
        adapter_version: "controlled-e2e",
        tags: [],
        fields: {},
      },
      title: `Quick capture ${publication}`,
      onReady: "import",
      candidates: [candidate],
      storage: {
        get: async () => Object.fromEntries(saved),
        set: async (values) => {
          for (const [key, value] of Object.entries(values)) saved.set(key, value);
        },
        remove: async (keys) => {
          for (const key of [keys].flat()) saved.delete(key);
        },
      },
      progress: () => {},
      download: async () => ({
        id: candidate.id,
        filename: candidate.filename,
        mediaType: "model/stl",
        file: new Blob([stlFor(publication)]),
      }),
    });
    const read = async () => (await page.request.get(`${api}/api/v1/inbox/${result.id}`)).json();
    await expect.poll(async () => (await read()).state, { timeout: 30000 }).toBe("completed");
    const item = await read();
    expect(item.resulting_model_id).toBeGreaterThan(0);
    await page.goto("/");
    await collapseNavigationSidebar(page);
    await page
      .getByRole("navigation", { name: "Navigation" })
      .getByRole("link", { name: "Pending", exact: true })
      .click();
    const sidebar = page.getByRole("complementary", { name: "Pending Imports" });
    await sidebar.getByRole("link", { name: "Open inbox" }).click();
    await expect(page).toHaveURL(/\/inbox$/);
    await page.getByRole("tab", { name: /Completed/ }).click();
    const row = page.locator("li").filter({ hasText: `Quick capture ${publication}` });
    await expect(row.getByRole("link", { name: "Open model" })).toHaveAttribute(
      "href",
      `/models/${item.resulting_model_id}`,
    );
  });

  test("recovers a browser transfer before reviewing every resulting Model", async ({ page }) => {
    const api = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;
    const suffix = randomUUID();
    const pairing = await page.request.post(`${api}/api/v1/browser-pairings`);
    expect(pairing.ok()).toBe(true);
    const { code } = await pairing.json();
    const device = await claimBrowserPairing({ vault: api, code, name: `Recovery ${suffix}` });
    const saved: Record<string, string> = {};
    const storage: Parameters<typeof captureSequentialFiles>[0]["storage"] = {
      get: async () =>
        Object.fromEntries(Object.entries(saved).map(([key, value]) => [key, JSON.parse(value)])),
      set: async (values) => {
        for (const [key, value] of Object.entries(values)) saved[key] = JSON.stringify(value);
      },
      remove: async (keys) => {
        for (const key of [keys].flat()) delete saved[key];
      },
    };
    const downloads: string[] = [];
    let interrupted = true;
    const candidates = ["first", "second"].map((id) => ({
      id,
      filename: `${id}-${suffix}.stl`,
      fileType: "stl" as const,
      profile: { id, name: `Profile ${id}`, origin: "provider" as const, estimated_seconds: 3600 },
    }));
    const options = {
      vault: api,
      authorization: device.deviceCredential,
      title: `Capture ${suffix}`,
      source: {
        provider: "printables" as const,
        canonical_url: "https://www.printables.com/model/321-benchy",
        source_item_id: "321",
        source_revision: null,
        adapter_version: "controlled-e2e",
        tags: [],
        fields: { title: { value: `Capture ${suffix}`, origin: "confirmed" as const } },
      },
      candidates,
      storage,
      progress: () => {},
      download: async (candidate: (typeof candidates)[number]) => {
        downloads.push(candidate.id);
        if (candidate.id === "second" && interrupted)
          throw new Error("Provider connection interrupted");
        return {
          id: candidate.id,
          filename: candidate.filename,
          mediaType: "model/stl",
          file: new Blob([stlFor(candidate.filename)]),
        };
      },
    };
    await expect(captureSequentialFiles(options)).rejects.toThrow(
      "Provider connection interrupted",
    );
    interrupted = false;
    const receipt = await captureSequentialFiles(options);
    expect(downloads).toEqual(["first", "second", "second"]);
    await page.goto(`/inbox/${receipt.id}`);
    await expect(page.getByRole("button", { name: "Edit destination" })).toBeVisible();
    await expect(page.getByText("Provider estimate", { exact: true })).toHaveCount(2);
    await page.getByRole("button", { name: "Import", exact: true }).click();
    await expect(page.getByRole("link", { name: "Open model", exact: true })).toHaveCount(3, {
      timeout: 30000,
    });
    const result = await (await page.request.get(`${api}/api/v1/inbox/${receipt.id}`)).json();
    expect(result.state).toBe("completed");
    expect(result.results.map((r: { state: string }) => r.state)).toEqual(["imported", "imported"]);
    await page.goto(`/models/${result.results[0].model_id}`);
    await page.setViewportSize({ width: 1265, height: 1000 });
    await page.getByRole("tab", { name: "Source info", exact: true }).click();
    const geometry = page.getByRole("region", { name: "Geometry checks" });
    await expect(geometry).toBeVisible();
    await expect(geometry.getByText(/Advisory geometry evidence only/)).toBeVisible();
    await page.getByRole("tab", { name: "Overview", exact: true }).click();
    await expect(geometry).toBeHidden();
    const profiles = page.getByRole("region", { name: "Profiles from this publication" });
    await expect(profiles.getByText("Provider estimate", { exact: true })).toHaveCount(2);
    const sibling = result.results[1];
    await profiles
      .getByRole("link", { name: `Open profile file: ${candidates[1].filename}` })
      .click();
    await expect(page).toHaveURL(
      new RegExp(`/models/${sibling.model_id}\\?tab=files&artifact=${sibling.file_id}`),
    );
    await expect(page.locator(`#artifact-${sibling.file_id}`)).toBeVisible();
    // Revocation also denies the importer-specific read capabilities.
    if (!device.device) throw new Error("Pairing did not return its device");
    expect(
      (await page.request.delete(`${api}/api/v1/browser-pairings/${device.device.id}`)).ok(),
    ).toBe(true);
    const revoked = await fetch(`${api}/api/v1/inbox/${receipt.id}/capture-upload`, {
      headers: { Authorization: `Bearer ${device.deviceCredential}` },
    });
    expect(revoked.status).toBe(401);
    for (const entry of result.results)
      expect((await page.request.delete(`${api}/api/v1/models/${entry.model_id}`)).ok()).toBe(true);
  });
});
