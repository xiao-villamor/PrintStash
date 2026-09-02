import { afterEach, describe, expect, it, vi } from "vitest";
import {
  MAKERWORLD_METADATA_ADAPTER_VERSION,
  MAKERWORLD_METADATA_FIXTURE_VERSION,
  MAKERWORLD_MAX_RESPONSE_BYTES,
  downloadMakerWorldCandidate,
  isAllowedMakerWorldDownloadUrl,
  makerWorldCaptureFromMetadata,
  parseMakerWorldMetadataResponse,
  requestMakerWorldLinksInMainWorld,
  requestMakerWorldMetadataInExtensionContext,
  requestMakerWorldMetadataInMainWorld,
  readBoundedMakerWorldResponse,
  selectMakerWorldCandidates,
  validateMakerWorldMetadataDto,
  validateMakerWorldResolvedLinks,
} from "../makerworld-capture.ts";

const MAKERWORLD_DESIGN_SERVICE_V1_FIXTURE = {
  data: {
    id: "1234",
    defaultInstanceId: "instance-default",
    instances: [
      { id: "instance-default", name: "cube—高.3mf", fileSize: 12 },
      { id: "instance-alt", name: "cube-alt.3mf", fileSize: 24 },
    ],
  },
};

const MAKERWORLD_ROOT_DESIGN_FIXTURE = {
  id: "1574312",
  title: "Root design",
  designCreator: "Hiro Maker",
  license: "CC BY-NC 4.0",
  defaultInstanceId: "instance-1",
  instances: [
    { id: "instance-1", title: "First package" },
    { id: "instance-2", title: "Second package" },
    { id: "instance-3", title: "Third package" },
    { id: "instance-4", title: "Fourth package" },
  ],
};

function liveRootDesignWithUnrelatedPayload() {
  const unrelatedDepth: Record<string, unknown> = {};
  let cursor = unrelatedDepth;
  for (let index = 0; index < 9; index += 1) {
    cursor.next = {};
    cursor = cursor.next as Record<string, unknown>;
  }
  const wide = Array.from({ length: 29 }, () =>
    Array.from({ length: 29 }, () => ({ first: 1, second: 2, third: 3 })),
  );
  return {
    id: "1574312",
    title: "Root design",
    designCreator: { uid: "8901", name: "Hiro Maker", handle: "hiro-maker" },
    license: "CC BY-NC 4.0",
    instances: [
      { id: "instance-1", title: "First package" },
      { id: "instance-2", title: "Second package" },
      { id: "instance-3", title: "Third package" },
      { id: "instance-4", title: "Fourth package" },
    ],
    unrelated: {
      deep: unrelatedDepth,
      wide,
      extra: Array.from({ length: 29 }, (_, index) => index),
      padding: Array.from({ length: 11 }, () => 0),
    },
  };
}

function payloadMetrics(value: unknown) {
  const queue: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }];
  let nodes = 0;
  let maxDepth = 0;
  let maxArrayLength = 0;
  while (queue.length > 0) {
    const current = queue.shift();
    if (!current) throw new Error("Missing payload node");
    nodes += 1;
    maxDepth = Math.max(maxDepth, current.depth);
    if (Array.isArray(current.value)) {
      maxArrayLength = Math.max(maxArrayLength, current.value.length);
      for (const child of current.value) queue.push({ value: child, depth: current.depth + 1 });
    } else if (current.value !== null && typeof current.value === "object") {
      for (const child of Object.values(current.value))
        queue.push({ value: child, depth: current.depth + 1 });
    }
  }
  return { nodes, maxDepth, maxArrayLength };
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("MakerWorld bounded capture", () => {
  it("maps a streamed metadata body over the supplied cap to response_too_large", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(new TextEncoder().encode('{"id":"1234"'));
              controller.enqueue(new Uint8Array(64));
              controller.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    await expect(
      requestMakerWorldMetadataInExtensionContext({
        fetchImpl,
        endpoint: "https://makerworld.com/api/v1/design-service/design/1234",
        sourceItemId: "1234",
        fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
        maxResponseBytes: 16,
      }),
    ).resolves.toMatchObject({ ok: false, code: "response_too_large" });
  });

  it("enumerates shaped instances without choosing the default package", () => {
    const metadata = parseMakerWorldMetadataResponse(MAKERWORLD_DESIGN_SERVICE_V1_FIXTURE, "1234");

    expect(metadata.fixtureVersion).toBe(MAKERWORLD_METADATA_FIXTURE_VERSION);
    expect(metadata.files.map(({ id }) => id)).toEqual(["instance-default", "instance-alt"]);
    expect(selectMakerWorldCandidates(metadata.files, [])).toEqual([]);
    expect(selectMakerWorldCandidates(metadata.files, ["instance-alt"])).toEqual([
      metadata.files[1],
    ]);
    expect(JSON.stringify(metadata)).not.toContain("downloadUrl");
  });

  it("accepts the live root design shape and enumerates every package without preselection", () => {
    const metadata = parseMakerWorldMetadataResponse(MAKERWORLD_ROOT_DESIGN_FIXTURE, "1574312");

    expect(metadata.source).toMatchObject({
      title: "Root design",
      creatorName: "Hiro Maker",
      licenseCode: "CC BY-NC 4.0",
    });
    expect(metadata.files.map(({ id }) => id)).toEqual([
      "instance-1",
      "instance-2",
      "instance-3",
      "instance-4",
    ]);
    expect(metadata.files.map(({ filename }) => filename)).toEqual([
      "First package.3mf",
      "Second package.3mf",
      "Third package.3mf",
      "Fourth package.3mf",
    ]);
    expect(selectMakerWorldCandidates(metadata.files, [])).toEqual([]);
  });

  it("projects consumed live fields while ignoring an unrelated deep and wide payload", async () => {
    const fixture = liveRootDesignWithUnrelatedPayload();
    const metrics = payloadMetrics(fixture);
    expect(metrics.maxArrayLength).toBe(29);
    expect(metrics.maxDepth).toBe(11);
    expect(metrics.nodes).toBeGreaterThanOrEqual(3467);
    expect(metrics.nodes).toBeLessThan(3500);
    expect(JSON.stringify(fixture).length).toBeLessThan(512 * 1024);

    const metadata = parseMakerWorldMetadataResponse(fixture, "1574312");
    expect(metadata.source).toMatchObject({
      creatorId: "8901",
      creatorName: "Hiro Maker",
      licenseCode: "CC BY-NC 4.0",
    });
    expect(metadata.files).toHaveLength(4);
    expect(metadata.files.map(({ filename }) => filename)).toEqual([
      "First package.3mf",
      "Second package.3mf",
      "Third package.3mf",
      "Fourth package.3mf",
    ]);
    expect(JSON.stringify(metadata)).not.toContain("unrelated");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(fixture)),
    );
    const isolated = new Function(
      `return (${requestMakerWorldMetadataInMainWorld.toString()})`,
    )() as typeof requestMakerWorldMetadataInMainWorld;
    const result = await isolated({
      endpoint: "https://makerworld.com/api/v1/design-service/design/1574312",
      sourceItemId: "1574312",
      fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
      maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
    });
    expect(result).toMatchObject({
      ok: true,
      metadata: {
        source: { creatorId: "8901", creatorName: "Hiro Maker" },
        files: [
          { id: "instance-1", filename: "First package.3mf" },
          { id: "instance-2", filename: "Second package.3mf" },
          { id: "instance-3", filename: "Third package.3mf" },
          { id: "instance-4", filename: "Fourth package.3mf" },
        ],
      },
    });
    expect(JSON.stringify(result)).not.toContain("unrelated");
  });

  it.each(["filename", "fileName"])(
    "rejects a malicious explicit %s in both pure and serialized projections",
    async (field) => {
      const fixture = {
        id: "1574312",
        instances: [{ id: "instance-1", [field]: "\u0000escape.3mf" }],
      };
      expect(() => parseMakerWorldMetadataResponse(fixture, "1574312")).toThrow(/filename changed/);

      vi.stubGlobal(
        "fetch",
        vi.fn(async () => response(fixture)),
      );
      const isolated = new Function(
        `return (${requestMakerWorldMetadataInMainWorld.toString()})`,
      )() as typeof requestMakerWorldMetadataInMainWorld;
      await expect(
        isolated({
          endpoint: "https://makerworld.com/api/v1/design-service/design/1574312",
          sourceItemId: "1574312",
          fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
          maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
        }),
      ).resolves.toMatchObject({ ok: false, code: "contract_changed" });
    },
  );

  it("fails closed for changed, oversized, deep, duplicate, and invalid-size fixtures", () => {
    expect(() => parseMakerWorldMetadataResponse({ data: {} }, "1234")).toThrow();
    expect(() =>
      parseMakerWorldMetadataResponse(
        {
          data: {
            id: "1234",
            instances: Array.from({ length: 65 }, (_, index) => ({
              id: `instance-${index}`,
              name: `${index}.3mf`,
            })),
          },
        },
        "1234",
      ),
    ).toThrow();
    const deep: Record<string, unknown> = {};
    let cursor = deep;
    for (let index = 0; index < 20; index += 1) {
      cursor.child = {};
      cursor = cursor.child as Record<string, unknown>;
    }
    expect(() => parseMakerWorldMetadataResponse(deep, "1234")).toThrow();
    expect(() =>
      parseMakerWorldMetadataResponse(
        {
          data: {
            id: "1234",
            instances: [
              { id: "same", name: "a.3mf" },
              { id: "same", name: "b.3mf" },
            ],
          },
        },
        "1234",
      ),
    ).toThrow();
    expect(() =>
      parseMakerWorldMetadataResponse(
        {
          data: {
            id: "1234",
            instances: [{ id: "large", name: "a.3mf", fileSize: 2 ** 40 }],
          },
        },
        "1234",
      ),
    ).toThrow();
  });

  it("revalidates the versioned DTO and exact selected link identity", () => {
    const dto = {
      fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
      sourceItemId: "1234",
      source: { title: "Cube" },
      files: [{ id: "instance-1", filename: "cube.3mf", fileType: "other" as const }],
    };
    expect(validateMakerWorldMetadataDto(dto, "1234")).toEqual(dto);
    const selected = dto.files;
    const links = [{ id: "instance-1", url: "https://makerworld.bblmw.com/cube.3mf?sig=x" }];
    expect(validateMakerWorldResolvedLinks(selected, links)).toEqual(links);
    expect(() => validateMakerWorldResolvedLinks(selected, [])).toThrow();
    expect(() =>
      validateMakerWorldResolvedLinks(selected, [...links, { id: "extra", url: links[0].url }]),
    ).toThrow();
    expect(() =>
      validateMakerWorldResolvedLinks(selected, [
        { id: "instance-1", url: "https://evil.example/cube.3mf" },
      ]),
    ).toThrow();
    expect(isAllowedMakerWorldDownloadUrl("http://makerworld.bblmw.com/file")).toBe(false);
    expect(isAllowedMakerWorldDownloadUrl("https://makerworld.bblmw.com/file")).toBe(true);
    expect(isAllowedMakerWorldDownloadUrl("https://evil.makerworld.com/file")).toBe(false);
  });

  it("builds only normalized source metadata and keeps the adapter version explicit", () => {
    const capture = makerWorldCaptureFromMetadata(
      {
        fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
        sourceItemId: "1234",
        source: { title: "Cube", creatorName: "Maker" },
        files: [{ id: "instance-1", filename: "cube—高.3mf", fileType: "other" }],
      },
      "https://makerworld.com/en/models/1234-cube",
      "Cube",
    );
    expect(capture.source.adapter_version).toBe(MAKERWORLD_METADATA_ADAPTER_VERSION);
    expect(capture.candidates[0]?.filename).toBe("cube—高.3mf");
    expect(JSON.stringify(capture)).not.toContain("downloadUrl");
  });
});

describe("MakerWorld MAIN-world seams", () => {
  it("returns a normalized fixture without exposing the provider payload", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          data: {
            ...MAKERWORLD_DESIGN_SERVICE_V1_FIXTURE.data,
            defaultInstanceId: "instance-default",
            instances: [{ id: "instance-default", name: "cube.3mf", fileSize: 4 }],
          },
        }),
      ),
    );
    const isolated = new Function(
      `return (${requestMakerWorldMetadataInMainWorld.toString()})`,
    )() as typeof requestMakerWorldMetadataInMainWorld;
    const result = await isolated({
      endpoint: "https://makerworld.com/api/v1/design-service/design/1234",
      sourceItemId: "1234",
      fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
      maxResponseBytes: 512 * 1024,
    });
    expect(result.ok).toBe(true);
    expect(JSON.stringify(result)).not.toContain('"instances"');
    expect(JSON.stringify(result)).not.toContain('"defaultInstanceId"');
  });

  it("parses the serialized MAIN-world seam when the design is returned at the JSON root", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(MAKERWORLD_ROOT_DESIGN_FIXTURE)),
    );
    const isolated = new Function(
      `return (${requestMakerWorldMetadataInMainWorld.toString()})`,
    )() as typeof requestMakerWorldMetadataInMainWorld;

    const result = await isolated({
      endpoint: "https://makerworld.com/api/v1/design-service/design/1574312",
      sourceItemId: "1574312",
      fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
      maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
    });

    expect(result).toMatchObject({
      ok: true,
      metadata: {
        sourceItemId: "1574312",
        source: {
          title: "Root design",
          creatorName: "Hiro Maker",
          licenseCode: "CC BY-NC 4.0",
        },
        files: [
          { id: "instance-1" },
          { id: "instance-2" },
          { id: "instance-3" },
          { id: "instance-4" },
        ],
      },
    });
  });

  it("rejects an actual streamed metadata body above the response limit", async () => {
    const chunk = new Uint8Array(MAKERWORLD_MAX_RESPONSE_BYTES);
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(chunk);
              controller.enqueue(new Uint8Array([0]));
              controller.close();
            },
          }),
        ),
    );
    vi.stubGlobal("fetch", fetchImpl);
    const isolated = new Function(
      `return (${requestMakerWorldMetadataInMainWorld.toString()})`,
    )() as typeof requestMakerWorldMetadataInMainWorld;
    await expect(
      isolated({
        endpoint: "https://makerworld.com/api/v1/design-service/design/1234",
        sourceItemId: "1234",
        fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
        maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
      }),
    ).resolves.toMatchObject({ ok: false, code: "response_too_large" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("resolves only selected links and rejects an unsafe final URL", async () => {
    const fetchImpl = vi.fn(async () =>
      response({ url: "https://evil.example/cube.3mf?signature=secret" }),
    );
    vi.stubGlobal("fetch", fetchImpl);
    const isolated = new Function(
      `return (${requestMakerWorldLinksInMainWorld.toString()})`,
    )() as typeof requestMakerWorldLinksInMainWorld;
    const result = await isolated({
      endpoint: "https://makerworld.com/api/v1/design-service/instance",
      selectedIds: ["instance-1"],
      maxResponseBytes: 512 * 1024,
    });
    expect(result).toMatchObject({ ok: false, code: "contract_changed" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("resolves an exact selected subset using the known data.url response shape", async () => {
    const requested: string[] = [];
    const requestOptions: RequestInit[] = [];
    const fetchImpl = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
      const url = String(input);
      requested.push(url);
      requestOptions.push(options ?? {});
      const id = url.includes("instance-alt") ? "instance-alt" : "instance-default";
      return response({
        data: { url: `https://makerworld.bblmw.com/${id}.3mf?signature=ephemeral` },
      });
    });
    vi.stubGlobal("fetch", fetchImpl);
    const isolated = new Function(
      `return (${requestMakerWorldLinksInMainWorld.toString()})`,
    )() as typeof requestMakerWorldLinksInMainWorld;
    const result = await isolated({
      endpoint: "https://makerworld.com/api/v1/design-service/instance",
      selectedIds: ["instance-alt", "instance-default"],
      maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
    });
    expect(result).toMatchObject({
      ok: true,
      links: [
        { id: "instance-alt", url: expect.stringContaining("signature=ephemeral") },
        { id: "instance-default", url: expect.stringContaining("signature=ephemeral") },
      ],
    });
    expect(requested).toEqual([
      "https://makerworld.com/api/v1/design-service/instance/instance-alt/f3mf?type=download",
      "https://makerworld.com/api/v1/design-service/instance/instance-default/f3mf?type=download",
    ]);
    expect(requestOptions[0]?.headers).toMatchObject({
      Accept: "application/json",
      "X-BBL-App-Source": "makerworld",
      "X-BBL-Client-Name": "MakerWorld",
      "X-BBL-Client-Type": "web",
      "X-BBL-Client-Version": "00.00.00.01",
    });
    expect(JSON.stringify(result)).toContain("signature=ephemeral");
  });

  it("accepts the current root-level url response shape", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          url: "https://makerworld.bblmw.com/1656140.3mf?signature=ephemeral",
        }),
      ),
    );
    const isolated = new Function(
      `return (${requestMakerWorldLinksInMainWorld.toString()})`,
    )() as typeof requestMakerWorldLinksInMainWorld;

    await expect(
      isolated({
        endpoint: "https://makerworld.com/api/v1/design-service/instance",
        selectedIds: ["1656140"],
        maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
      }),
    ).resolves.toEqual({
      ok: true,
      links: [
        {
          id: "1656140",
          url: "https://makerworld.bblmw.com/1656140.3mf?signature=ephemeral",
        },
      ],
    });
  });

  it("classifies MakerWorld HTTP 418 as a browser challenge", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response({}, 418)),
    );
    const isolated = new Function(
      `return (${requestMakerWorldLinksInMainWorld.toString()})`,
    )() as typeof requestMakerWorldLinksInMainWorld;

    await expect(
      isolated({
        endpoint: "https://makerworld.com/api/v1/design-service/instance",
        selectedIds: ["1656140"],
        maxResponseBytes: MAKERWORLD_MAX_RESPONSE_BYTES,
      }),
    ).resolves.toMatchObject({ ok: false, code: "challenge" });
  });

  it("rejects an unsafe final redirect and accepts a bounded allowlisted stream", async () => {
    const candidate = {
      id: "instance-alt",
      filename: "cube-alt.3mf",
      fileType: "other" as const,
      sizeBytes: 4,
    };
    const unsafeResponse = new Response("mesh", {
      headers: { "Content-Length": "4" },
    });
    Object.defineProperty(unsafeResponse, "url", {
      configurable: true,
      value: "https://evil.example/cube-alt.3mf",
    });
    await expect(
      downloadMakerWorldCandidate({
        candidate,
        link: "https://makerworld.bblmw.com/cube-alt.3mf?signature=ephemeral",
        fetchImpl: vi.fn(async () => unsafeResponse),
      }),
    ).rejects.toThrow(/unsafe host/);

    const allowedResponse = new Response("mesh", {
      headers: { "Content-Length": "4", "Content-Type": "model/3mf" },
    });
    Object.defineProperty(allowedResponse, "url", {
      configurable: true,
      value: "https://makerworld.bblmw.com/cube-alt.3mf",
    });
    const granted: string[] = [];
    const downloaded = await downloadMakerWorldCandidate({
      candidate,
      link: "https://makerworld.bblmw.com/cube-alt.3mf?signature=ephemeral",
      fetchImpl: vi.fn(async () => allowedResponse),
      ensureOriginPermission: async (origin) => {
        granted.push(origin);
      },
    });
    expect(downloaded.file.size).toBe(4);
    expect(granted).toEqual(["https://makerworld.bblmw.com/*"]);
  });

  it("aborts a provider download on timeout before bytes arrive", async () => {
    const controller = new AbortController();
    const fetchImpl = vi.fn(
      async (_input: URL | RequestInfo, init?: RequestInit): Promise<Response> => {
        const signal = init?.signal;
        if (!signal) throw new Error("missing abort signal");
        return new Promise<Response>((_resolve, reject) => {
          if (signal.aborted) {
            reject(new DOMException("The operation was aborted.", "AbortError"));
            return;
          }
          signal.addEventListener(
            "abort",
            () => reject(new DOMException("The operation was aborted.", "AbortError")),
            { once: true },
          );
        });
      },
    );
    const pending = downloadMakerWorldCandidate({
      candidate: {
        id: "instance-alt",
        filename: "cube-alt.3mf",
        fileType: "other",
        sizeBytes: 4,
      },
      link: "https://makerworld.bblmw.com/cube-alt.3mf?signature=ephemeral",
      fetchImpl,
      signal: controller.signal,
      ensureOriginPermission: async () => {},
    });
    setTimeout(() => controller.abort(), 0);
    await expect(pending).rejects.toThrow("aborted");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("caps streamed package bytes and detects changed declared sizes", async () => {
    const changed = new Response(new Uint8Array([1, 2, 3]), {
      headers: { "Content-Length": "4" },
    });
    await expect(readBoundedMakerWorldResponse(changed, 4)).rejects.toThrow(/size changed/);
    const oversized = new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(new Uint8Array(512 * 1024 * 1024 + 1));
          controller.close();
        },
      }),
    );
    await expect(readBoundedMakerWorldResponse(oversized)).rejects.toThrow(/too large/);
  });
});
