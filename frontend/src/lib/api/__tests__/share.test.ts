/**
 * Share links: handing one model to somebody who has no account here.
 *
 * The URL builders are the security-relevant half. They are handed to an `<img>`
 * or an `<a>`, which cannot send an `Authorization` header, so the share token has
 * to travel in the path. That makes each of these paths a capability, and a
 * builder that pointed at the authenticated route instead would render a broken
 * image for the recipient and a working one for the owner — which is the failure
 * that never gets noticed before the link is sent.
 *
 * A model's share list is read fresh: a revoked link that still shows in the UI is
 * a link somebody believes still works.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getSessionVersion } from "@/lib/session-transport";
import { getUser, storeLogin } from "@/lib/auth-store";
import {
  getSharedModel,
  createModelShare,
  listModelShares,
  revokeShare,
  sharedDownloadUrl,
  sharedGcodeUrl,
  sharedStlUrl,
  sharedThumbnailUrl,
} from "@/lib/api/share";

import { expectRequest, fetchMock, lastCall, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();

  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getSharedModel", () => {
  it("omits private credentials from a capability lookup", async () => {
    storeLogin("fixture-token", { id: 1, username: "admin", email: null, is_superuser: true });
    respondWith({
      name: "Public boat",
      files: [],
      allow_download: false,
      description: null,
      has_thumbnail: false,
    });
    const result = await getSharedModel("abc");
    expect(result.name).toBe("Public boat");
    expect(lastCall().init).toMatchObject({ cache: "no-store", credentials: "omit" });
    expect(new Headers(lastCall().init.headers).has("Authorization")).toBe(false);
  });
  it("cancels a public lookup when its caller leaves", async () => {
    let signal: AbortSignal | null | undefined;
    fetchMock.mockImplementation(
      (_url, init) =>
        new Promise<Response>((_resolve, reject) => {
          signal = init?.signal;
          signal?.addEventListener("abort", () => reject(signal?.reason), { once: true });
        }),
    );
    const controller = new AbortController();
    const pending = getSharedModel("abc", { signal: controller.signal });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    controller.abort();
    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(signal?.aborted).toBe(true);
  });
  it("preserves private identity when a capability lookup is unauthorized", async () => {
    storeLogin("fixture-token", { id: 1, username: "admin", email: null, is_superuser: true });
    const incarnation = getSessionVersion();
    respondWith({ detail: "not_found" }, 401);
    await expect(getSharedModel("abc")).rejects.toMatchObject({ status: 401 });
    expect(getUser()?.username).toBe("admin");
    expect(getSessionVersion()).toBe(incarnation);
  });
});

describe("createModelShare", () => {
  it("POSTs a link under the model it shares", async () => {
    respondWith({ id: 1, token: "abc" });

    await createModelShare(4, { expires_in_days: 7, allow_download: true });

    expectRequest("/api/v1/models/4/shares", "POST");
  });
});

describe("listModelShares", () => {
  it("reads a model's links fresh", async () => {
    respondWith([]);

    await listModelShares(4);

    // A revoked link that still shows is a link somebody thinks still works.
    expect(lastCall().init).toMatchObject({ cache: "no-store" });
  });
});

describe("revokeShare", () => {
  it("revokes one by id", async () => {
    respondWith(null, 204);

    await revokeShare(9);

    expectRequest("/api/v1/shares/9", "DELETE");
  });
});

describe("public URL builders", () => {
  // These are handed to an <img>/<a>, which cannot send an Authorization header,
  // so the token has to be in the path.
  it.each([
    { label: "thumbnail", build: () => sharedThumbnailUrl("a/b?c"), suffix: "/thumbnail" },
    { label: "mesh", build: () => sharedStlUrl("a/b?c", 2), suffix: "/files/2/stl" },
    { label: "download", build: () => sharedDownloadUrl("a/b?c", 2), suffix: "/files/2/download" },
    { label: "toolpath", build: () => sharedGcodeUrl("a/b?c", 2), suffix: "/files/2/toolpath" },
  ])("encodes a capability segment for $label", ({ build, suffix }) => {
    expect(build()).toBe(`/api/v1/share/a%2Fb%3Fc${suffix}`);
  });
  it("puts the token in the thumbnail path", () => {
    expect(sharedThumbnailUrl("abc")).toBe("/api/v1/share/abc/thumbnail");
  });

  it("puts the token in the mesh path", () => {
    expect(sharedStlUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/stl");
  });

  it("puts the token in the download path", () => {
    expect(sharedDownloadUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/download");
  });

  it("puts the token in the G-code path", () => {
    expect(sharedGcodeUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/toolpath");
  });
});
