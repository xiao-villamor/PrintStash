import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ingestUrl, selectCollectionMembers, selectModelFiles } from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

/**
 * Pin the collection / multi-file import API client to the exact wire contract
 * the backend ingest router expects: paths, verbs, and request bodies. A drift
 * here silently breaks the URL-import review flows.
 */

/** Any payload the API can serialise as a JSON response body. */
type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue };

const fetchMock = vi.fn<typeof fetch>();

/**
 * Answer every fetch with a real 200 Response, freshly built per call because a
 * response body can only be read once.
 */
function respondWith(data: JsonValue): void {
  fetchMock.mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } }),
    ),
  );
}

interface FetchCall {
  /** The API client always calls fetch with a path string, never a Request. */
  url: string;
  init: RequestInit;
  /** The request body decoded back from the JSON the client serialised. */
  body: JsonValue;
}

function lastCall(): FetchCall {
  const [input, init] = fetchMock.mock.calls.at(-1)!;
  return {
    url: String(input),
    init: init ?? {},
    body: init?.body == null ? null : JSON.parse(String(init.body)),
  };
}

const queued = { job_id: "job-1", state: "pending", message: "ingestion queued" };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ingestUrl", () => {
  it("POSTs the review flag for collection imports", async () => {
    respondWith(queued);

    await ingestUrl({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });

    const { url, init, body } = lastCall();
    expect(url).toBe("/api/v1/ingest/url");
    expect(init).toMatchObject({ method: "POST" });
    expect(body).toMatchObject({
      url: "https://www.printables.com/@u/collections/3525050",
      review: true,
    });
  });
});

describe("selectModelFiles", () => {
  it("POSTs the chosen file ids to the files token endpoint", async () => {
    respondWith(queued);

    await selectModelFiles("tok-files", {
      file_ids: ["10", "11"],
      collection: "Cats",
    });

    const { url, init, body } = lastCall();
    expect(url).toBe("/api/v1/ingest/url/files/tok-files/select");
    expect(init).toMatchObject({ method: "POST" });
    expect(body).toEqual({
      file_ids: ["10", "11"],
      collection: "Cats",
    });
  });
});

describe("selectCollectionMembers", () => {
  it("POSTs the chosen member ids to the collection token endpoint", async () => {
    respondWith(queued);

    await selectCollectionMembers("tok-coll", { member_ids: ["1", "2"] });

    const { url, init, body } = lastCall();
    expect(url).toBe("/api/v1/ingest/collection/tok-coll/select");
    expect(init).toMatchObject({ method: "POST" });
    expect(body).toEqual({ member_ids: ["1", "2"] });
  });
});
