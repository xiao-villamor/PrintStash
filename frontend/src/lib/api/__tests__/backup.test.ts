/**
 * Creating, listing, restoring, and downloading a whole-library backup.
 *
 * `downloadBackup` is the reason this file exists in the shape it does: it is the
 * only API client that drives the browser's own download machinery rather than
 * returning data. It builds an object URL, synthesises an anchor, clicks it, and
 * has to clean both up — a leaked blob URL pins the entire archive in memory for
 * the tab's lifetime, and a leaked anchor accumulates in the document on every
 * download.
 *
 * The filename is the visible half of that contract. The server names the file in
 * `Content-Disposition`; when it does not, the fallback has to still be something
 * a user can find on their disk rather than an id.
 *
 * A backup id is a timestamp, so every path that carries one has to survive URL
 * encoding — `restoreBackup` is pinned on the encoded form for that reason.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { createBackup, downloadBackup, listBackups, restoreBackup } from "@/lib/api/backup";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, respondWith } from "./_wire";

/** Replace the object-URL machinery, recording what the client created and released. */
function stubDownload() {
  const created: string[] = [];
  const revoked: string[] = [];
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: () => {
      created.push("blob:x");
      return "blob:x";
    },
    revokeObjectURL: (url: string) => revoked.push(url),
  });
  return { created, revoked };
}

/** Record the `download` attribute of every anchor the client clicks. */
function recordClicks(): string[] {
  const clicked: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    },
  );
  return clicked;
}

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("createBackup", () => {
  it("asks the server for a new archive", async () => {
    respondWith({ backup_id: "b1" });

    await createBackup();

    expectRequest("/api/v1/backups", "POST");
  });
});

describe("listBackups", () => {
  it("reads the archives the server holds", async () => {
    respondWith([]);

    await listBackups();

    expectRequest("/api/v1/backups");
  });
});

describe("restoreBackup", () => {
  it("restores one by id", async () => {
    respondWith({ backup_id: "b1", restored_files: 3 });

    await restoreBackup("2026-01-01T00:00:00Z");

    // The id is a timestamp, so it has to survive URL encoding.
    expectRequest("/api/v1/backups/2026-01-01T00%3A00%3A00Z/restore", "POST");
  });
});

describe("downloadBackup", () => {
  it("uses the filename the server named", async () => {
    fetchMock.mockResolvedValue(
      new Response("archive", {
        status: 200,
        headers: { "content-disposition": 'attachment; filename="printstash-b1.tar.gz"' },
      }),
    );
    stubDownload();
    const clicked = recordClicks();

    await downloadBackup("b1");

    expect(clicked).toEqual(["printstash-b1.tar.gz"]);
  });

  it("falls back to a sensible filename when the server names none", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    stubDownload();
    const clicked = recordClicks();

    await downloadBackup("b1");

    expect(clicked).toEqual(["printstash-backup-b1.tar.gz"]);
  });

  it("releases the object URL it created", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    const { revoked } = stubDownload();
    recordClicks();

    await downloadBackup("b1");

    // A leaked blob URL pins the whole archive in memory for the tab's life.
    expect(revoked).toEqual(["blob:x"]);
  });

  it("leaves no anchor behind in the document", async () => {
    fetchMock.mockResolvedValue(new Response("archive", { status: 200 }));
    stubDownload();
    recordClicks();

    await downloadBackup("b1");

    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
  });
});
