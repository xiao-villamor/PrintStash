import { afterEach, describe, expect, it, vi } from "vitest";
import {
  THINGIVERSE_MAX_FILE_SIZE_BYTES,
  downloadThingiverseCandidate,
  requestThingiverseFilesInMainWorld,
  thingiverseCaptureFromPage,
  validateThingiverseFiles,
} from "../thingiverse-capture.ts";

afterEach(() => {
  vi.unstubAllGlobals();
  document.body.replaceChildren();
});

describe("Thingiverse browser capture", () => {
  it("discovers individual model files and deduplicates repeated controls", () => {
    document.body.innerHTML = `
      <section><h3>Cable Mount.stl</h3><a href="/download:991001">Download</a></section>
      <section><a href="https://www.thingiverse.com/download:991001" title="Cable Mount.stl">Download again</a></section>
      <section><h3>Bracket.stl</h3><a href="https://www.thingiverse.com/download:991002" title="Bracket.stl">Download</a></section>
      <a href="https://evil.example/download:991003" download="bad.stl">Bad</a>
      <a href="/thing:7401604/zip">Download all</a>
    `;
    const isolated = new Function(
      `return (${requestThingiverseFilesInMainWorld.toString()})`,
    )() as typeof requestThingiverseFilesInMainWorld;

    expect(isolated({ sourceItemId: "7401604" })).toEqual({
      ok: true,
      files: [
        {
          id: "991001",
          filename: "Cable Mount.stl",
          fileType: "stl",
          url: "https://www.thingiverse.com/download:991001",
        },
        {
          id: "991002",
          filename: "Bracket.stl",
          fileType: "stl",
          url: "https://www.thingiverse.com/download:991002",
        },
      ],
    });
  });

  it("builds selectable candidates without retaining download URLs", () => {
    const files = validateThingiverseFiles({
      ok: true,
      files: [
        {
          id: "991001",
          filename: "Cable Mount.stl",
          fileType: "stl",
          url: "https://www.thingiverse.com/download:991001",
        },
      ],
    });
    const capture = thingiverseCaptureFromPage({
      pageUrl: "https://www.thingiverse.com/thing:7401604/files",
      pageTitle: "Cable Mount - Screwable or Glueable",
      jsonLd: [],
      files,
    });

    expect(capture).toMatchObject({
      state: "ready",
      source: { provider: "thingiverse", source_item_id: "7401604" },
      candidates: [
        {
          id: "thingiverse:7401604:file:991001",
          filename: "Cable Mount.stl",
          fileType: "stl",
        },
      ],
    });
    expect(JSON.stringify(capture)).not.toContain("/download:991001");
  });

  it("discovers a later individual download control", () => {
    document.body.innerHTML = `<a href="https://www.thingiverse.com/download:991002" title="Bracket.stl">Download</a>`;
    expect(requestThingiverseFilesInMainWorld({ sourceItemId: "7401604" })).toEqual({
      ok: true,
      files: [
        {
          id: "991002",
          filename: "Bracket.stl",
          fileType: "stl",
          url: "https://www.thingiverse.com/download:991002",
        },
      ],
    });
  });

  it("rejects an unsafe file DTO", () => {
    expect(() =>
      validateThingiverseFiles({
        ok: true,
        files: [
          {
            id: "991001",
            filename: "../Cable Mount.stl",
            fileType: "stl",
            url: "https://evil.example/download:991001",
          },
        ],
      }),
    ).toThrow(/contract changed/);
  });

  it("downloads one bounded selected file", async () => {
    const response = new Response("solid mesh", {
      status: 200,
      headers: { "Content-Length": "10", "Content-Type": "model/stl" },
    });
    Object.defineProperty(response, "url", {
      configurable: true,
      value: "https://cdn.thingiverse.com/assets/991001/Cable_Mount.stl",
    });
    const fetchImpl = vi.fn(async () => response);
    const granted: string[][] = [];

    const downloaded = await downloadThingiverseCandidate({
      candidate: {
        id: "thingiverse:7401604:file:991001",
        filename: "Cable Mount.stl",
        fileType: "stl",
      },
      link: "https://www.thingiverse.com/download:991001",
      fetchImpl,
      ensureOriginPermissions: async (origins) => {
        granted.push(origins);
      },
    });

    expect(fetchImpl).toHaveBeenCalledWith(
      "https://www.thingiverse.com/download:991001",
      expect.objectContaining({ credentials: "include", cache: "no-store" }),
    );
    expect(downloaded).toMatchObject({
      id: "thingiverse:7401604:file:991001",
      filename: "Cable Mount.stl",
      mediaType: "model/stl",
    });
    expect(downloaded.file.size).toBe(10);
    expect(granted).toEqual([
      [
        "https://thingiverse.com/*",
        "https://www.thingiverse.com/*",
        "https://cdn.thingiverse.com/*",
        "https://api.thingiverse.com/*",
      ],
    ]);
  });

  it("rejects HTML, oversized files, and redirects outside official hosts", async () => {
    const candidate = {
      id: "thingiverse:7401604:file:991001",
      filename: "Cable Mount.stl",
      fileType: "stl" as const,
    };
    const challenge = new Response("<html>Just a moment...</html>", {
      headers: { "Content-Type": "text/html" },
    });
    Object.defineProperty(challenge, "url", {
      configurable: true,
      value: "https://www.thingiverse.com/download:991001",
    });
    await expect(
      downloadThingiverseCandidate({
        candidate,
        link: "https://www.thingiverse.com/download:991001",
        fetchImpl: vi.fn(async () => challenge),
      }),
    ).rejects.toThrow(/browser check/);

    const oversized = new Response("mesh", {
      headers: { "Content-Length": String(THINGIVERSE_MAX_FILE_SIZE_BYTES + 1) },
    });
    Object.defineProperty(oversized, "url", {
      configurable: true,
      value: "https://cdn.thingiverse.com/file.stl",
    });
    await expect(
      downloadThingiverseCandidate({
        candidate,
        link: "https://www.thingiverse.com/download:991001",
        fetchImpl: vi.fn(async () => oversized),
      }),
    ).rejects.toThrow(/too large/);

    const unsafe = new Response("mesh");
    Object.defineProperty(unsafe, "url", {
      configurable: true,
      value: "https://evil.example/file.stl",
    });
    await expect(
      downloadThingiverseCandidate({
        candidate,
        link: "https://www.thingiverse.com/download:991001",
        fetchImpl: vi.fn(async () => unsafe),
      }),
    ).rejects.toThrow(/unsafe host/);
  });
});
