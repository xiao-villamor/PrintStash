/** Interactive searches have bounded waits and preserve navigation cancellation. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cancelInferenceDownload,
  deleteInferenceModel,
  downloadInferenceModel,
  getSearchPreferences,
  getSearchSettings,
  saveSearchSettings,
  getSearchStatus,
  listInferenceModels,
  listSearchGenerations,
  estimateSearchGeneration,
  importEnvironmentEndpoint,
  parseSearch,
  searchImage,
  searchLibrary,
  searchUsingModel,
  validateInferenceModel,
} from "@/lib/api/search";
import { json } from "@/test-support/render";
import { searchResponse, searchConfiguration } from "@/test-support/search";

import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

const fetcher = vi.fn<typeof fetch>();
describe("Interactive search requests", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    fetcher.mockReset();
    vi.stubGlobal("fetch", fetcher);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });
  function stall() {
    fetcher.mockImplementation(
      (_url, options) =>
        new Promise<Response>((_resolve, reject) => {
          const signal = options?.signal;
          if (signal?.aborted) reject(signal.reason);
          else signal?.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
  }
  it.each([
    { label: "text search", run: () => searchLibrary({ q: "bracket" }) },
    {
      label: "image search",
      run: () => searchImage(new File(["image"], "query.png", { type: "image/png" })),
    },
    { label: "related model search", run: () => searchUsingModel(12) },
    { label: "filter interpretation", run: () => parseSearch("bracket") },
    { label: "search preferences", run: () => getSearchPreferences() },
  ])("ends a stalled $label with a recoverable timeout", async ({ run }) => {
    stall();
    await Promise.all([
      expect(run()).rejects.toMatchObject({ code: "search_timeout", status: 408 }),
      vi.advanceTimersByTimeAsync(30_000),
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("cancels retrieval when navigation aborts", async () => {
    stall();
    const controller = new AbortController();
    await Promise.all([
      expect(searchLibrary({ q: "bracket" }, controller.signal)).rejects.toMatchObject({
        name: "AbortError",
      }),
      Promise.resolve().then(() => controller.abort()),
    ]);
    expect(vi.getTimerCount()).toBe(0);
  });
  it.each([
    { label: "status", run: (signal: AbortSignal) => getSearchStatus({ signal }) },
    { label: "settings", run: (signal: AbortSignal) => getSearchSettings({ signal }) },
    { label: "models", run: (signal: AbortSignal) => listInferenceModels({ signal }) },
    { label: "generations", run: (signal: AbortSignal) => listSearchGenerations({ signal }) },
    { label: "preferences", run: (signal: AbortSignal) => getSearchPreferences({ signal }) },
    {
      label: "estimate",
      run: (signal: AbortSignal) =>
        estimateSearchGeneration(
          {
            local_model_id: "small",
            index_backend: "auto",
            quantization: "float32",
            auto_activate: true,
          },
          { signal },
        ),
    },
  ])("cancels an active $label read", async ({ run }) => {
    stall();
    const controller = new AbortController();
    const pending = run(controller.signal);
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("rejects an already cancelled search", async () => {
    stall();
    const controller = new AbortController();
    controller.abort();
    await expect(searchLibrary({ q: "bracket" }, controller.signal)).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("releases the deadline after successful retrieval", async () => {
    const response = searchResponse();
    fetcher.mockResolvedValue(json(response));
    expect(await searchLibrary({ q: "bracket" })).toEqual(response);
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves server errors", async () => {
    fetcher.mockResolvedValue(json({ detail: "search_unavailable" }, 503));
    await expect(searchLibrary({ q: "bracket" })).rejects.toMatchObject({
      code: "search_unavailable",
      status: 503,
    });
    expect(vi.getTimerCount()).toBe(0);
  });
  it("preserves network errors", async () => {
    fetcher.mockRejectedValue(new TypeError("Network unavailable"));
    await expect(searchLibrary({ q: "bracket" })).rejects.toThrow("Network unavailable");
    expect(vi.getTimerCount()).toBe(0);
  });
});

/** Local model management: each call is one administrator request, never cached. */
describe("Local model management", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("answers a download request with the Job to follow", async () => {
    respondWith({ job_id: "download-1" }, 202);

    await expect(downloadInferenceModel("text/small v1")).resolves.toEqual({
      job_id: "download-1",
    });

    expectRequest("/api/v1/inference/models/text%2Fsmall%20v1/download", "POST");
    expect(lastBody()).toEqual({});
  });

  it("cancels a download by its Job", async () => {
    respondWith(null, 204);

    await cancelInferenceDownload("job/1");

    expectRequest("/api/v1/inference/models/downloads/job%2F1/cancel", "POST");
  });

  it("validates an installed model", async () => {
    respondWith({ id: "small", ready: true });

    await expect(validateInferenceModel("small")).resolves.toEqual({ id: "small", ready: true });

    expectRequest("/api/v1/inference/models/small/validate", "POST");
  });

  it("deletes an installed model", async () => {
    respondWith(null, 204);

    await deleteInferenceModel("small");

    expectRequest("/api/v1/inference/models/small", "DELETE");
  });

  it("imports an endpoint the environment declares", async () => {
    respondWith({ id: 3 }, 201);

    await importEnvironmentEndpoint("chat");

    expectRequest("/api/v1/config/ai-search/endpoints/from-environment/chat", "POST");
    expect(lastBody()).toEqual({});
  });
});

describe("Conditional Search settings transport", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => vi.unstubAllGlobals());

  it("sends the captured editing base", async () => {
    const original = searchConfiguration({ edit_version: 5 });
    fetchMock.mockResolvedValue(json({ ...original, edit_version: 6 }));
    await saveSearchSettings(original.settings, original);
    const headers = new Headers(lastCall().init.headers);
    expect(headers.get("If-Match")).toBe(`"search-settings-e${original.edit_epoch}-v5"`);
    expect(headers.get("X-PrintStash-Edit-Contract")).toBe("conditional-v1");
    expect(lastBody()).toEqual(original.settings);
  });

  it.each([
    { label: "non-advancing", edit_version: 1, edit_epoch: "a".repeat(32) },
    { label: "other history", edit_version: 2, edit_epoch: "b".repeat(32) },
  ])("refuses a $label acknowledgement", async ({ edit_version, edit_epoch }) => {
    const original = searchConfiguration();
    fetchMock.mockResolvedValue(json(searchConfiguration({ edit_version, edit_epoch })));
    await expect(saveSearchSettings(original.settings, original)).rejects.toThrow(
      "Invalid editing acknowledgement",
    );
  });
});
