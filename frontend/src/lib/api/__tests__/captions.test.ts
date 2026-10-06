/** Caption clients preserve conditional action intent and caller cancellation. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getCaption, patchCaption } from "@/lib/api/captions";
import { aCaption } from "@/test-support/captions";
import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());
describe("caption endpoint", () => {
  it.each(["model", "collection", "multipart_model", "document"] as const)(
    "reads the %s caption fresh",
    async (type) => {
      respondWith({ ...aCaption() });
      expect(await getCaption(type, 7)).toEqual(aCaption());
      expectRequest(`/api/v1/subjects/${type}/7/caption`, "GET");
      expect(lastCall().init.cache).toBe("no-store");
    },
  );
  it("cancels an active caption read", async () => {
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
        }),
    );
    const controller = new AbortController();
    const pending = getCaption("model", 7, { signal: controller.signal });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(signal?.aborted).toBe(true);
  });
  it("preserves the captured edit version", async () => {
    const result = aCaption({ text: "Server-normalized text", version_token: "b".repeat(32) });
    respondWith({ ...result });
    expect(
      await patchCaption("model", 7, {
        action: "edit",
        text: "My draft",
        version_token: "a".repeat(32),
      }),
    ).toEqual(result);
    expectRequest("/api/v1/subjects/model/7/caption", "PATCH");
    expect(lastBody()).toEqual({ action: "edit", text: "My draft", version_token: "a".repeat(32) });
  });
  it.each(["dismiss", "reset", "generate"] as const)(
    "preserves the captured %s action",
    async (action) => {
      respondWith({ ...aCaption() });
      await patchCaption("document", 9, { action, version_token: "a".repeat(32) });
      expectRequest("/api/v1/subjects/document/9/caption", "PATCH");
      expect(lastBody()).toEqual({ action, version_token: "a".repeat(32) });
    },
  );
  it("retains the existing initial-caption protocol", async () => {
    respondWith({ ...aCaption() });
    await patchCaption("model", 7, { action: "edit", text: "Initial caption" });
    expect(lastBody()).toEqual({ action: "edit", text: "Initial caption" });
  });
});
