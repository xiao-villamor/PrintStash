/** A Source draft conflicts against the real Model version, then saves only after review. */
import { createHash } from "node:crypto";
import { test, expect } from "./helpers";
import { gcodeFor } from "./util";
import type { ModelProvenanceRead, InboxItem } from "../../src/types";

const API = `http://127.0.0.1:${process.env.PLAYWRIGHT_REAL_API_PORT ?? 8410}`;

test.describe("Source editing", () => {
  test("edits captured metadata with real conflict recovery", async ({ page }) => {
    const name = `e2e-source-${Date.now()}`;
    const sourceUrl = `https://www.printables.com/model/1234-${name}`;
    const bytes = Buffer.from(gcodeFor(name));
    const captured = await page.request.post(`${API}/api/v1/inbox/capture-upload-slots`, {
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
            id: "gcode",
            filename: `${name}.gcode`,
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
      const uploaded = await page.request.put(
        `${API}/api/v1/inbox/capture-upload-slots/${slots.slots[0].id}`,
        { headers: { "Content-Type": "application/octet-stream" }, data: bytes },
      );
      expect(uploaded.ok(), await uploaded.text()).toBe(true);
      const finalized = await page.request.post(
        `${API}/api/v1/inbox/${itemId}/capture-upload-finalize`,
      );
      expect(finalized.ok(), await finalized.text()).toBe(true);
      const imported = await page.request.post(`${API}/api/v1/inbox/${itemId}/import`, {
        data: { selected_ids: ["gcode"] },
      });
      expect(imported.ok(), await imported.text()).toBe(true);
      await expect(async () => {
        const item: InboxItem = await (
          await page.request.get(`${API}/api/v1/inbox/${itemId}`)
        ).json();
        expect(item.state).toBe("completed");
        modelId = item.results[0]?.model_id ?? null;
        expect(modelId).not.toBeNull();
      }).toPass({ timeout: 30_000 });
      const readPath = `/api/v1/models/${modelId}/provenance`;
      const initial: ModelProvenanceRead = await (
        await page.request.get(`${API}${readPath}`)
      ).json();
      const writePath = `${readPath}/${initial.sources[0].id}`;
      await page.goto(`/models/${modelId}`);
      await page.getByRole("tab", { name: "Source", exact: true }).click();
      await page.getByRole("button", { name: "Edit Title" }).click();
      const draft = page.getByRole("textbox", { name: "Title override" });
      await draft.fill("My reviewed source draft");
      const competing = await page.request.patch(`${API}${writePath}`, {
        headers: {
          "If-Match": `"model-${modelId}-v${initial.edit_version}"`,
          "X-PrintStash-Edit-Contract": "conditional-v1",
        },
        data: { overrides: { title: "Another editor's source title" }, clear_overrides: [] },
      });
      expect(competing.ok(), await competing.text()).toBe(true);
      const current: ModelProvenanceRead = await competing.json();
      const rejected = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === writePath && response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "Save", exact: true }).click();
      expect((await rejected).status()).toBe(412);
      await expect(draft).toHaveValue("My reviewed source draft");
      await expect(page.getByRole("button", { name: "Save", exact: true })).toBeDisabled();
      await page.getByRole("button", { name: "Review latest version" }).click();
      await expect(
        page
          .getByRole("region", { name: "Latest saved version" })
          .getByText("Another editor's source title"),
      ).toBeVisible();
      const confirmed = page.waitForResponse(
        (response) =>
          new URL(response.url()).pathname === writePath && response.request().method() === "PATCH",
      );
      await page.getByRole("button", { name: "Save my draft against this version" }).click();
      const acknowledgement = await confirmed;
      expect(acknowledgement.ok()).toBe(true);
      expect(await acknowledgement.request().headerValue("If-Match")).toBe(
        `"model-${modelId}-v${current.edit_version}"`,
      );
      await expect(page.getByText("My reviewed source draft", { exact: true })).toBeVisible();
      const persisted: ModelProvenanceRead = await (
        await page.request.get(`${API}${readPath}`)
      ).json();
      expect(
        persisted.sources[0].fields.find((field) => field.field_name === "title")?.effective_value,
      ).toBe("My reviewed source draft");
      expect(persisted.edit_version).toBeGreaterThan(current.edit_version);
    } finally {
      if (modelId !== null) await page.request.delete(`${API}/api/v1/models/${modelId}`);
      await page.request.delete(`${API}/api/v1/inbox/${itemId}`);
    }
  });
});
