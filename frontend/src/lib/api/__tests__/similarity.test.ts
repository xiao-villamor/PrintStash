/** Similarity clients preserve scopes, review identity, cursor and every privacy-sensitive filter. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { invalidateApiCache } from "@/lib/api/request";
import {
  cancelSimilarityRun,
  decideSimilarity,
  findModelSimilar,
  getSimilarityCandidate,
  getSimilarityRun,
  getSimilarityStatus,
  listSimilarityCandidates,
  listSimilarityRuns,
  saveSimilaritySettings,
  startSimilarityRun,
  searchSimilarModels,
  previewSimilaritySelection,
} from "@/lib/api/similarity";
import { expectRequest, fetchMock, lastBody, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  respondWith({});
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("listSimilarityCandidates", () => {
  it("retains all candidate filters", async () => {
    await listSimilarityCandidates({
      collection_id: 3,
      file_type: "3mf",
      source: "external",
      known_good: false,
      review_state: "later",
      freshness: "stale",
      evidence_class: "repaired",
      minimum_confidence: 0,
      model_id: 8,
      cursor: "[0.9,12]",
      limit: 25,
    });
    const params = new URL(lastCall().url, "http://test").searchParams;
    expect(Object.fromEntries(params)).toEqual({
      collection_id: "3",
      file_type: "3mf",
      source: "external",
      known_good: "false",
      review_state: "later",
      freshness: "stale",
      evidence_class: "repaired",
      minimum_confidence: "0",
      model_id: "8",
      cursor: "[0.9,12]",
      limit: "25",
    });
  });
});
describe("similarity reads", () => {
  it.each([
    { label: "status", load: () => getSimilarityStatus(), path: "/api/v1/similarity/status" },
    {
      label: "candidate",
      load: () => getSimilarityCandidate(7),
      path: "/api/v1/similarity/candidates/7",
    },
    { label: "run", load: () => getSimilarityRun(8), path: "/api/v1/similarity/runs/8" },
    { label: "history", load: () => listSimilarityRuns(), path: "/api/v1/similarity/runs" },
    {
      label: "older history",
      load: () => listSimilarityRuns(4),
      path: "/api/v1/similarity/runs?before_id=4",
    },
  ])("reads $label fresh", async ({ load, path }) => {
    await load();
    expectRequest(path);
    expect(lastCall().init.cache).toBe("no-store");
  });
});
describe("similarity commands", () => {
  it("starts the selected Model scope", async () => {
    await startSimilarityRun("models", [2, 4]);
    expectRequest("/api/v1/similarity/runs", "POST");
    expect(lastBody()).toEqual({ scope: "models", ids: [2, 4] });
  });
  it("starts a whole-library scan", async () => {
    await startSimilarityRun("library");
    expect(lastBody()).toEqual({ scope: "library", ids: [] });
  });
  it("requests cancellation", async () => {
    await cancelSimilarityRun(8);
    expectRequest("/api/v1/similarity/runs/8/cancel", "POST");
  });
  it("queries missing Model work", async () => {
    await findModelSimilar(3);
    expectRequest("/api/v1/models/3/similar/query", "POST");
  });
  it("patches only changed settings", async () => {
    await saveSimilaritySettings({ enabled: false });
    expectRequest("/api/v1/similarity/settings", "PATCH");
    expect(lastBody()).toEqual({ enabled: false });
  });
  it("preserves decision replay identity", async () => {
    await decideSimilarity(7, { action: "confirm_evidence", request_id: "intent-1", version: 4 });
    expectRequest("/api/v1/similarity/candidates/7/decision", "POST");
    expect(lastBody()).toEqual({ action: "confirm_evidence", request_id: "intent-1", version: 4 });
  });
});

describe("similarity reader lifetime", () => {
  it.each([
    { label: "status", read: (signal: AbortSignal) => getSimilarityStatus({ signal }) },
    { label: "candidate", read: (signal: AbortSignal) => getSimilarityCandidate(1, { signal }) },
    { label: "queue", read: (signal: AbortSignal) => listSimilarityCandidates({}, { signal }) },
    { label: "run", read: (signal: AbortSignal) => getSimilarityRun(1, { signal }) },
    { label: "history", read: (signal: AbortSignal) => listSimilarityRuns(undefined, { signal }) },
    {
      label: "semantic neighbors",
      read: (signal: AbortSignal) => searchSimilarModels({ model_id: 1 }, { signal }),
    },
    {
      label: "selection preview",
      read: (signal: AbortSignal) =>
        previewSimilaritySelection({ minimum_confidence: 0.9, class_overrides: {} }, { signal }),
    },
  ])("aborts an active $label read", async ({ read }) => {
    const controller = new AbortController();
    fetchMock.mockImplementation(
      (_url, options) =>
        new Promise((_resolve, reject) => {
          const signal = options?.signal;
          if (!signal) throw new Error("Signal required");
          signal.addEventListener("abort", () => reject(signal.reason), { once: true });
        }),
    );
    const outcome = read(controller.signal).catch((error: Error) => error);
    controller.abort();
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
  });
});
