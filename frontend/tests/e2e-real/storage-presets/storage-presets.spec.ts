/** Named hosted-file presets retain identity while using the real read-only WebDAV source. */
import { test, expect } from "../helpers";
import { gcodeFor } from "../util";

const webdavBase = `http://127.0.0.1:${process.env.PLAYWRIGHT_STORAGE_WEBDAV_PORT ?? 8775}`;

test.describe("storage presets", () => {
  test("persists a hosted WebDAV preset as a read-only Library source", async ({ page }) => {
    const name = `koofr-preset-${Date.now()}`;
    const root = `preset-${Date.now()}`;
    const password = "preset-app-password";
    const body = Buffer.from(gcodeFor(name));
    const remoteUrl = `${webdavBase}/${root}/${name}.gcode`;
    let connectionId: number | undefined;
    let libraryId: number | undefined;
    expect((await page.request.fetch(`${webdavBase}/${root}`, { method: "MKCOL" })).ok()).toBe(
      true,
    );
    expect((await page.request.put(remoteUrl, { data: body })).ok()).toBe(true);
    try {
      await page.goto("/settings?section=remote-storage");
      const remote = page.getByRole("region", { name: "Remote storage" });
      const connectionReads: string[] = [];
      page.on("request", (request) => {
        if (
          request.method() === "GET" &&
          new URL(request.url()).pathname === "/api/v1/storage-connections"
        )
          connectionReads.push(request.url());
      });
      await remote
        .getByRole("group", { name: "Storage category" })
        .getByRole("button", { name: "Nextcloud and WebDAV" })
        .click();
      await remote
        .getByRole("group", { name: "Provider" })
        .getByRole("button", { name: "Koofr — WebDAV" })
        .click();
      await page.getByLabel("Connection name").fill(name);
      await remote
        .getByRole("group", { name: "Use for" })
        .getByRole("button", { name: "Library sources" })
        .click();
      await page.getByLabel("Server URL").fill(webdavBase);
      await page.getByLabel("Base folder", { exact: true }).fill(root);
      await page.getByLabel("Username", { exact: true }).fill("webdav-user");
      await page.getByLabel("Password", { exact: true }).fill(password);
      const settledReads = connectionReads.length;
      const saved = page.waitForResponse(
        (response) =>
          response.url().endsWith("/api/v1/storage-connections") &&
          response.request().method() === "POST",
      );
      await page.getByRole("button", { name: "Save connection" }).click();
      const response = await saved;
      expect(response.status()).toBe(201);
      const connection = await response.json();
      connectionId = connection.id;
      expect(connection).toMatchObject({
        kind: "webdav",
        purpose: "library",
        configuration: { provider: "koofr" },
      });
      expect(JSON.stringify(connection)).not.toContain(password);
      await expect(remote.getByRole("listitem").filter({ hasText: name })).toBeVisible();
      await expect(page.getByLabel("Password", { exact: true })).toHaveValue("");
      expect(connectionReads).toHaveLength(settledReads);

      await page.reload();
      const row = page.getByRole("listitem").filter({ hasText: name });
      await expect(row).toBeVisible();
      const persisted = await page.request.get("/api/v1/storage-connections");
      expect(
        (await persisted.json()).find((item: { id: number }) => item.id === connectionId),
      ).toMatchObject({ kind: "webdav", configuration: { provider: "koofr" } });
      expect(await persisted.text()).not.toContain(password);
      const readsBeforeProbe = connectionReads.length;
      const checked = page.waitForResponse((result) =>
        result.url().endsWith(`/storage-connections/${connectionId}/probe`),
      );
      await row.getByRole("button", { name: "Test", exact: true }).click();
      expect((await checked).ok()).toBe(true);
      await expect(page.getByText(`${name} is reachable.`, { exact: true })).toBeVisible();
      expect(connectionReads).toHaveLength(readsBeforeProbe);

      expect(
        (
          await page.request.put("/api/v1/config", { data: { external_libraries_enabled: true } })
        ).ok(),
      ).toBe(true);
      const created = await page.request.post("/api/v1/libraries", {
        data: {
          name,
          source_kind: "webdav",
          connection_id: connectionId,
          scan_schedule: "",
          watch_mode: "off",
        },
      });
      expect(created.status()).toBe(201);
      const library = await created.json();
      libraryId = library.id;
      expect(library.writeback_enabled).toBe(false);
      expect((await page.request.post(`/api/v1/libraries/${libraryId}/scan`)).ok()).toBe(true);
      await expect
        .poll(
          async () => (await (await page.request.get(`/api/v1/models?q=${name}`)).json()).length,
        )
        .toBe(1);
      const models = await (await page.request.get(`/api/v1/models?q=${name}`)).json();
      const model = await (await page.request.get(`/api/v1/models/${models[0].id}`)).json();
      const file = model.files.find(
        (item: { original_filename: string }) => item.original_filename === `${name}.gcode`,
      );
      expect(file).toBeDefined();
      const downloaded = await page.request.get(`/api/v1/files/${file.id}/download`);
      expect(downloaded.ok()).toBe(true);
      expect(await downloaded.body()).toEqual(body);
      expect(await (await page.request.get(remoteUrl)).body()).toEqual(body);
    } finally {
      if (libraryId !== undefined) await page.request.delete(`/api/v1/libraries/${libraryId}`);
      if (connectionId !== undefined)
        await page.request.delete(`/api/v1/storage-connections/${connectionId}`);
      await page.request.delete(`${webdavBase}/${root}`);
      await page.request.put("/api/v1/config", { data: { external_libraries_enabled: false } });
    }
  });
});
