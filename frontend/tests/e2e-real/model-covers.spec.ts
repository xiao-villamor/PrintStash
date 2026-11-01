/** A selected cover persists through real thumbnail regeneration and reload. */
import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import { test, expect } from "./helpers";
import { stlFor } from "./util";
import type { InboxItem, ModelRead, ModelProvenanceRead } from "../../src/types";

test.describe("Model cover controls", () => {
  test("preserves a chosen cover through regeneration", async ({ page }) => {
    const name = `e2e-cover-${Date.now()}`;
    const sourceUrl = `https://www.printables.com/model/1234-${name}`;
    const bytes = Buffer.from(stlFor(name));
    const png = execFileSync(
      resolve("../backend/.venv/bin/python"),
      ["-c", "import sys; from tests.factories.content import png; sys.stdout.buffer.write(png())"],
      { cwd: resolve("../backend") },
    );
    // ── Import a real mesh with provenance ──
    const captured = await page.request.post("/api/v1/inbox/capture-upload-slots", {
      data: {
        source_url: sourceUrl,
        capture_source: {
          provider: "printables",
          canonical_url: sourceUrl,
          source_item_id: "1234",
          source_revision: null,
          adapter_version: "browser-test-v1",
          fields: { title: { value: name, origin: "confirmed" } },
          tags: [],
        },
        files: [
          {
            id: "mesh",
            filename: `${name}.stl`,
            media_type: "application/octet-stream",
            size_bytes: bytes.length,
            sha256: createHash("sha256").update(bytes).digest("hex"),
          },
        ],
      },
    });
    expect(captured.status(), await captured.text()).toBe(201);
    const slots = await captured.json();
    const itemId: number = slots.item.id;
    let modelId: number | null = null;
    try {
      expect(
        (
          await page.request.put(`/api/v1/inbox/capture-upload-slots/${slots.slots[0].id}`, {
            headers: { "Content-Type": "application/octet-stream" },
            data: bytes,
          })
        ).ok(),
      ).toBe(true);
      expect(
        (await page.request.post(`/api/v1/inbox/${itemId}/capture-upload-finalize`)).ok(),
      ).toBe(true);
      expect(
        (
          await page.request.post(`/api/v1/inbox/${itemId}/import`, {
            data: { selected_ids: ["mesh"] },
          })
        ).ok(),
      ).toBe(true);
      await expect(async () => {
        const item: InboxItem = await (await page.request.get(`/api/v1/inbox/${itemId}`)).json();
        expect(item.state).toBe("completed");
        modelId = item.results[0]?.model_id ?? null;
        expect(modelId).not.toBeNull();
      }).toPass({ timeout: 30_000 });
      const path = `/api/v1/models/${modelId}`;
      const provenance: ModelProvenanceRead = await (
        await page.request.get(path + "/provenance")
      ).json();
      const sourcePath = path + `/provenance/${provenance.sources[0].id}/cover`;
      expect(
        (
          await page.request.put(sourcePath, {
            multipart: { file: { name: "source.png", mimeType: "image/png", buffer: png } },
          })
        ).ok(),
      ).toBe(true);
      await page.goto(`/models/${modelId}`);
      await expect(page.getByRole("button", { name: "Screenshot", exact: true })).toBeEnabled({
        timeout: 60_000,
      });

      // ── Source image ──
      await page.getByRole("button", { name: "Edit cover", exact: true }).click();
      const editor = page.getByRole("dialog", { name: "Model cover", exact: true });
      await editor.getByRole("button", { name: "Use image from printables" }).click();
      await expect(editor).toHaveCount(0);
      const fromSource: ModelRead = await (await page.request.get(path)).json();
      expect(fromSource.has_custom_cover).toBe(true);
      const sourceBytes = await (await page.request.get(sourcePath + "/content")).body();
      expect(await (await page.request.get(fromSource.thumbnail_url!)).body()).toEqual(sourceBytes);

      // ── Upload ──
      await page.getByRole("button", { name: "Edit cover", exact: true }).click();
      await editor.getByLabel("Upload image", { exact: true }).setInputFiles({
        name: "chosen.png",
        mimeType: "image/png",
        buffer: png,
      });
      await expect(editor).toHaveCount(0);
      const uploaded: ModelRead = await (await page.request.get(path)).json();
      expect(uploaded.thumbnail_url).not.toBe(fromSource.thumbnail_url);

      // ── Capture the real WebGL view ──
      await page.getByRole("button", { name: "Edit cover", exact: true }).click();
      await editor.getByRole("button", { name: "Use current 3D view" }).click();
      await expect(editor).toHaveCount(0);
      const chosen: ModelRead = await (await page.request.get(path)).json();
      expect(chosen.has_custom_cover).toBe(true);
      const chosenBytes = await (await page.request.get(chosen.thumbnail_url!)).body();
      expect(chosenBytes.length).toBeGreaterThan(png.length);
      expect(chosen.thumbnail_url).not.toBe(uploaded.thumbnail_url);

      // ── Regenerate the automatic thumbnail while keeping the chosen cover ──
      const frames: string[] = [];
      page.on("websocket", (socket) =>
        socket.on("framereceived", (frame) => frames.push(String(frame.payload))),
      );
      await page.reload();
      await expect(page.getByRole("button", { name: "Edit cover", exact: true })).toBeVisible();
      const regenerated = await page.request.post(
        "/api/v1/admin/work/derivatives/thumbnail/regenerate",
        {
          data: { mode: "all" },
        },
      );
      expect(regenerated.status(), await regenerated.text()).toBe(202);
      await expect
        .poll(
          () =>
            frames.some((payload) => {
              const notice = JSON.parse(payload);
              return (
                notice.type === "derivative" &&
                notice.model_id === modelId &&
                notice.kind === "thumbnail" &&
                notice.state === "ready"
              );
            }),
          { timeout: 60_000 },
        )
        .toBe(true);
      const after: ModelRead = await (await page.request.get(path)).json();
      expect(after.thumbnail_url).toBe(chosen.thumbnail_url);
      expect(await (await page.request.get(after.thumbnail_url!)).body()).toEqual(chosenBytes);

      // ── Reset reveals the regenerated automatic thumbnail ──
      await page.getByRole("button", { name: "Edit cover", exact: true }).click();
      await editor.getByRole("button", { name: "Reset to automatic" }).click();
      await expect(editor).toHaveCount(0);
      await page.reload();
      const reset: ModelRead = await (await page.request.get(path)).json();
      expect(reset.has_custom_cover).toBe(false);
      expect(reset.thumbnail_url).toMatch(/^\/api\/v1\/files\/\d+\/thumbnail/);
    } finally {
      if (modelId !== null) await page.request.delete(`/api/v1/models/${modelId}`);
      await page.request.delete(`/api/v1/inbox/${itemId}`);
    }
  });
});
