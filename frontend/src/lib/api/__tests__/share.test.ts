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

import { invalidateApiCache } from "@/lib/api/request";
import {
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
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
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
    expect(sharedGcodeUrl("abc", 2)).toBe("/api/v1/share/abc/files/2/gcode");
  });
});
