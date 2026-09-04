/**
 * Getting a whole library out of one PrintStash and into another.
 *
 * These two downloads are the only model clients that drive the browser's own save
 * machinery rather than returning data. They build an object URL, synthesise an
 * anchor, click it, and have to release the URL afterwards — a leaked blob URL
 * pins an entire library archive in memory for the life of the tab.
 *
 * The filename is the user-visible half. A model export takes whatever the server
 * named it and falls back to a name derived from the format; the library archive
 * uses a fixed portable name, because the point of the file is to be recognisable
 * on the machine it is carried to.
 *
 * The import direction is a multipart upload, and the same rule applies as
 * everywhere else: a JSON-serialised object type-checks and arrives unparseable.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  downloadLibraryArchive,
  downloadModelExport,
  importLibraryArchive,
} from "@/lib/api/models";
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastCall, respondWith } from "../_wire";

/** Replace the browser save path, recording the filenames and the released URLs. */
function stubBrowserSave() {
  const revoked: string[] = [];
  vi.stubGlobal("URL", {
    ...URL,
    createObjectURL: () => "blob:x",
    revokeObjectURL: (url: string) => revoked.push(url),
  });
  const clicked: string[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
    function (this: HTMLAnchorElement) {
      clicked.push(this.download);
    },
  );
  return { clicked, revoked };
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

describe("downloadModelExport", () => {
  it("uses the filename the server named", async () => {
    fetchMock.mockResolvedValue(
      new Response("json", {
        status: 200,
        headers: { "content-disposition": 'attachment; filename="named.json"' },
      }),
    );
    const { clicked } = stubBrowserSave();

    await downloadModelExport("json");

    expect(clicked).toEqual(["named.json"]);
  });

  it("falls back to a name derived from the format", async () => {
    fetchMock.mockResolvedValue(new Response("csv", { status: 200 }));
    const { clicked } = stubBrowserSave();

    await downloadModelExport("csv");

    expect(clicked).toEqual(["printstash-model-export.csv"]);
  });

  it("asks for the format the caller chose", async () => {
    fetchMock.mockResolvedValue(new Response("csv", { status: 200 }));
    stubBrowserSave();

    await downloadModelExport("csv");

    expect(lastCall().url).toBe("/api/v1/models/export?format=csv");
  });
});

describe("downloadLibraryArchive", () => {
  it("saves the archive under its portable name", async () => {
    fetchMock.mockResolvedValue(new Response("zip", { status: 200 }));
    const { clicked } = stubBrowserSave();

    await downloadLibraryArchive();

    // A fixed name, because the archive is meant to be recognisable on another
    // machine.
    expect(clicked).toEqual(["printstash-library-v1.zip"]);
  });

  it("releases the object URL after saving", async () => {
    fetchMock.mockResolvedValue(new Response("zip", { status: 200 }));
    const { revoked } = stubBrowserSave();

    await downloadLibraryArchive();

    expect(revoked).toEqual(["blob:x"]);
  });
});

describe("importLibraryArchive", () => {
  it("POSTs the archive as multipart", async () => {
    respondWith({ job_id: "abc", state: "pending" });

    await importLibraryArchive(new File(["zip"], "library.zip"));

    expectRequest("/api/v1/models/library-import", "POST");
    expect(lastCall().init.body).toBeInstanceOf(FormData);
  });
});
