/** Cache administration uses explicit mutation routes and fresh effective-policy reads. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { artifactCacheApi } from "@/lib/api/artifact-cache";
import { anArtifactCache } from "@/test-support/factories";
import { expectRequest, fetchMock, lastBody } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();

  fetchMock.mockImplementation(() =>
    Promise.resolve(new Response(JSON.stringify(anArtifactCache({ edit_version: 2 })))),
  );
});
afterEach(() => vi.unstubAllGlobals());

describe("artifactCacheApi", () => {
  it("reads current policy without reusing a prior response", async () => {
    await artifactCacheApi.read();
    const current = await artifactCacheApi.read();
    expectRequest("/api/v1/config/artifact-cache");
    expect(fetchMock.mock.calls).toHaveLength(2);
    expect(current.policy).toEqual(anArtifactCache().policy);
  });

  it("persists the complete cache policy", async () => {
    const policy = anArtifactCache().policy;
    await artifactCacheApi.save(policy, { base: anArtifactCache() });
    expectRequest("/api/v1/config/artifact-cache", "PUT");
    expect(lastBody()).toEqual(policy);
  });

  it("reads effective defaults after resetting overrides", async () => {
    const current = await artifactCacheApi.reset({ base: anArtifactCache() });
    expect(fetchMock.mock.calls.map(([url, init]) => [String(url), init?.method ?? "GET"])).toEqual(
      [["/api/v1/config/artifact-cache", "DELETE"]],
    );
    expect(current.source).toBe("environment");
  });

  it("clears cache through the explicit clear action", async () => {
    await artifactCacheApi.clear();
    expectRequest("/api/v1/config/artifact-cache/clear", "POST");
  });
});
