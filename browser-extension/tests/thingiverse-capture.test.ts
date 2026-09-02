import { afterEach, describe, expect, it, vi } from "vitest";
import {
  THINGIVERSE_MAX_FILE_SIZE_BYTES,
  downloadThingiverseArchive,
  thingiverseCaptureFromPage,
} from "../thingiverse-capture.ts";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Thingiverse browser capture", () => {
  it("offers the official model archive as an automatic candidate", () => {
    const capture = thingiverseCaptureFromPage({
      pageUrl: "https://www.thingiverse.com/thing:7401604/files",
      pageTitle: "Cable Mount - Screwable or Glueable",
      jsonLd: [],
    });

    expect(capture).toMatchObject({
      state: "ready",
      source: { provider: "thingiverse", source_item_id: "7401604" },
      candidates: [
        {
          id: "thingiverse:7401604:archive",
          filename: "thingiverse-7401604.zip",
          fileType: "other",
        },
      ],
    });
  });

  it("downloads a bounded official archive", async () => {
    const response = new Response("PK\u0003\u0004", {
      status: 200,
      headers: { "Content-Length": "4", "Content-Type": "application/zip" },
    });
    Object.defineProperty(response, "url", {
      configurable: true,
      value: "https://cdn.thingiverse.com/zip/7401604.zip",
    });
    const fetchImpl = vi.fn(async () => response);
    const granted: string[] = [];

    const downloaded = await downloadThingiverseArchive({
      sourceItemId: "7401604",
      fetchImpl,
      ensureOriginPermission: async (origin) => {
        granted.push(origin);
      },
    });

    expect(fetchImpl).toHaveBeenCalledWith(
      "https://www.thingiverse.com/thing:7401604/zip",
      expect.objectContaining({ credentials: "include", cache: "no-store" }),
    );
    expect(downloaded).toMatchObject({
      id: "thingiverse:7401604:archive",
      filename: "thingiverse-7401604.zip",
      mediaType: "application/zip",
    });
    expect(downloaded.file.size).toBe(4);
    expect(granted).toEqual(["https://www.thingiverse.com/*"]);
  });

  it("rejects an HTML browser challenge", async () => {
    const response = new Response("<html>Just a moment...</html>", {
      status: 200,
      headers: { "Content-Type": "text/html" },
    });
    Object.defineProperty(response, "url", {
      configurable: true,
      value: "https://www.thingiverse.com/thing:7401604/zip",
    });

    await expect(
      downloadThingiverseArchive({
        sourceItemId: "7401604",
        fetchImpl: vi.fn(async () => response),
      }),
    ).rejects.toThrow(/browser check/);
  });

  it("rejects an oversized archive before reading bytes", async () => {
    const response = new Response("PK", {
      status: 200,
      headers: {
        "Content-Length": String(THINGIVERSE_MAX_FILE_SIZE_BYTES + 1),
        "Content-Type": "application/zip",
      },
    });
    Object.defineProperty(response, "url", {
      configurable: true,
      value: "https://cdn.thingiverse.com/zip/7401604.zip",
    });

    await expect(
      downloadThingiverseArchive({
        sourceItemId: "7401604",
        fetchImpl: vi.fn(async () => response),
      }),
    ).rejects.toThrow(/too large/);
  });

  it("rejects a redirect outside official hosts", async () => {
    const response = new Response("PK\u0003\u0004", {
      status: 200,
      headers: { "Content-Type": "application/zip" },
    });
    Object.defineProperty(response, "url", {
      configurable: true,
      value: "https://evil.example/7401604.zip",
    });

    await expect(
      downloadThingiverseArchive({
        sourceItemId: "7401604",
        fetchImpl: vi.fn(async () => response),
      }),
    ).rejects.toThrow(/unsafe host/);
  });
});
