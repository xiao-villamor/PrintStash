/** Browser transfers require an approved CDN and retain the original bytes. */
import { describe, it, expect, vi } from "vitest";
import { downloadMarketplaceFile } from "../marketplace-download.ts";
const candidate = { id: "profile", filename: "model.3mf", fileType: "other" as const };
describe("downloadMarketplaceFile", () => {
  it("preserves approved file bytes", async () => {
    const bytes = new Uint8Array([80, 75, 3, 4]);
    const result = await downloadMarketplaceFile({
      candidate,
      url: "https://files.example.test/model.3mf",
      provider: "Test",
      allowedHosts: ["files.example.test"],
      ensureOriginPermission: async () => {},
      fetchImpl: async () => new Response(bytes),
    });
    expect(new Uint8Array(await result.file.arrayBuffer())).toEqual(bytes);
  });
  it("does not compare decoded bytes with compressed Content-Length", async () => {
    const result = await downloadMarketplaceFile({
      candidate,
      url: "https://files.example.test/model.3mf",
      provider: "Test",
      allowedHosts: ["files.example.test"],
      ensureOriginPermission: async () => {},
      fetchImpl: async () =>
        new Response("decoded file", {
          headers: { "Content-Length": "5", "Content-Encoding": "gzip" },
        }),
    });
    expect(await result.file.text()).toBe("decoded file");
  });
  it("rejects a CDN lookalike before fetching", async () => {
    const request = vi.fn();
    await expect(
      downloadMarketplaceFile({
        candidate,
        url: "https://files.example.test.evil.test/model.3mf",
        provider: "Test",
        allowedHosts: ["files.example.test"],
        ensureOriginPermission: async () => {},
        fetchImpl: request,
      }),
    ).rejects.toThrow("download host");
    expect(request).not.toHaveBeenCalled();
  });
  it("rejects an oversized declared body", async () => {
    await expect(
      downloadMarketplaceFile({
        candidate,
        url: "https://files.example.test/model.3mf",
        provider: "Test",
        allowedHosts: ["files.example.test"],
        ensureOriginPermission: async () => {},
        fetchImpl: async () =>
          new Response("small", { headers: { "content-length": String(512 * 1024 * 1024 + 1) } }),
      }),
    ).rejects.toThrow("transfer limit");
  });
});
