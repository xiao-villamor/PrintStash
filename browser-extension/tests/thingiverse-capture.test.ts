import { afterEach, describe, expect, it, vi } from "vitest";
import {
  THINGIVERSE_MAX_FILE_SIZE_BYTES,
  THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
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
  it("lists individual files from the current page API without rendered links", async () => {
    document.body.innerHTML = `<button aria-label="Download Cable Mount.stl">Download</button>`;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              files: [
                {
                  id: 991001,
                  name: "Cable Mount.stl",
                  public_url: "https://www.thingiverse.com/download:991001",
                },
                {
                  id: 991002,
                  name: "Bracket.3mf",
                  public_url: "https://www.thingiverse.com/download:991002",
                },
              ],
            }),
            { headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    const isolated = new Function(
      `return (${requestThingiverseFilesInMainWorld.toString()})`,
    )() as typeof requestThingiverseFilesInMainWorld;

    await expect(
      isolated({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({
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
          filename: "Bracket.3mf",
          fileType: "other",
          url: "https://www.thingiverse.com/download:991002",
        },
      ],
    });
  });

  it("deduplicates legacy rendered download controls", async () => {
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

    await expect(
      isolated({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({
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

  it("discovers a later individual download control", async () => {
    document.body.innerHTML = `<a href="https://www.thingiverse.com/download:991002" title="Bracket.stl">Download</a>`;
    await expect(
      requestThingiverseFilesInMainWorld({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({
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

  it("rejects an oversized Thingiverse metadata response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response("{}", {
            headers: { "Content-Length": String(THINGIVERSE_MAX_METADATA_RESPONSE_BYTES + 1) },
          }),
      ),
    );

    await expect(
      requestThingiverseFilesInMainWorld({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({ ok: false, code: "response_too_large" });
  });

  it("reports a Thingiverse page API challenge", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => new Response("<html>Just a moment...</html>", { status: 403 })),
    );

    await expect(
      requestThingiverseFilesInMainWorld({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({ ok: false, code: "challenge" });
  });

  it("rejects a malformed current Thingiverse file response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              files: [
                {
                  id: 991001,
                  name: "../Cable Mount.stl",
                  public_url: "https://www.thingiverse.com/download:991001",
                },
              ],
            }),
            { headers: { "Content-Type": "application/json" } },
          ),
      ),
    );

    await expect(
      requestThingiverseFilesInMainWorld({
        sourceItemId: "7401604",
        endpoint: "https://www.thingiverse.com/api/v2/things/7401604/complete",
        maxResponseBytes: THINGIVERSE_MAX_METADATA_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({ ok: false, code: "contract_changed" });
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

  it("rejects an HTML browser challenge instead of treating it as a model file", async () => {
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
  });

  it("rejects an oversized selected model file", async () => {
    const candidate = {
      id: "thingiverse:7401604:file:991001",
      filename: "Cable Mount.stl",
      fileType: "stl" as const,
    };
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
  });

  it("rejects a selected model file redirected outside official hosts", async () => {
    const candidate = {
      id: "thingiverse:7401604:file:991001",
      filename: "Cable Mount.stl",
      fileType: "stl" as const,
    };
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
