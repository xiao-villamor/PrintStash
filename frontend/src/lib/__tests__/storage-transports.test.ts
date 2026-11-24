/**
 * Remote storage kinds come from one exhaustive map. The connection form and
 * the Library-source picker read it, so a kind missing here would be rejected
 * as input or never offered.
 */

import { describe, expect, it } from "vitest";

import {
  REMOTE_STORAGE_KINDS,
  isLibrarySourceKind,
  isRemoteStorageKind,
  remoteStorageKindPresentation,
} from "@/lib/storage-transports";

describe("REMOTE_STORAGE_KINDS", () => {
  it("lists every remote transport in picker order", () => {
    expect(REMOTE_STORAGE_KINDS).toEqual(["s3", "webdav", "sftp", "gdrive"]);
  });
});

describe("isRemoteStorageKind", () => {
  it("accepts a registered remote transport", () => {
    expect(isRemoteStorageKind("webdav")).toBe(true);
  });

  it("rejects the mounted source kind", () => {
    expect(isRemoteStorageKind("mounted")).toBe(false);
  });

  it("rejects an inherited object key", () => {
    expect(isRemoteStorageKind("toString")).toBe(false);
  });
});

describe("isLibrarySourceKind", () => {
  it("accepts the mounted source kind", () => {
    expect(isLibrarySourceKind("mounted")).toBe(true);
  });

  it("rejects an unknown transport", () => {
    expect(isLibrarySourceKind("ftp")).toBe(false);
  });
});

describe("remoteStorageKindPresentation", () => {
  it("labels each kind for the source picker", () => {
    expect(
      REMOTE_STORAGE_KINDS.map((kind) => remoteStorageKindPresentation(kind).sourceLabel()),
    ).toEqual(["S3 / compatible", "WebDAV / Nextcloud", "SFTP", "Google Drive"]);
  });

  it("marks only Google Drive connections as beta", () => {
    expect(REMOTE_STORAGE_KINDS.filter((kind) => remoteStorageKindPresentation(kind).beta)).toEqual(
      ["gdrive"],
    );
  });
});
