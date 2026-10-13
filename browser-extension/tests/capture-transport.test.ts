import { describe, expect, it, vi } from "vitest";

import {
  CAPTURE_CLEANUP_TIMEOUT_MS,
  CAPTURE_MAX_FILE_SIZE_BYTES,
  CAPTURE_MAX_TOTAL_SIZE_BYTES,
  captureRichFiles,
  type CaptureStageRunner,
} from "../capture-transport.ts";

describe("capture upload-slot transport", () => {
  it("dismisses an unfinished capture after an upload failure", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          item: { id: 44 },
          slots: [
            {
              id: "slot-a",
              role: "file",
              source_file_id: "part",
              filename: "part.stl",
              media_type: "model/stl",
              size_bytes: 1,
              sha256: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    await expect(
      captureRichFiles({
        fetchImpl,
        vault: "https://prints.example.com",
        authorization: "credential",
        sourceUrl: "https://www.printables.com/model/9",
        captureSource: {
          provider: "printables",
          canonical_url: "https://www.printables.com/model/9",
          source_item_id: "9",
          source_revision: null,
          adapter_version: "browser-visible-v1",
          tags: [],
          fields: {},
        },
        files: [
          { id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" },
        ],
      }),
    ).rejects.toThrow("while uploading part.stl");
    expect(fetchImpl).toHaveBeenNthCalledWith(
      3,
      "https://prints.example.com/api/v1/inbox/44/capture-upload",
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it.each([
    ["staging_capacity_exceeded", "capture capacity is full"],
    ["staging_capacity_unavailable", "could not measure staging space"],
  ])("explains the server's %s response", async (detail, message) => {
    const fetchImpl = vi.fn().mockResolvedValue(Response.json({ detail }, { status: 507 }));
    await expect(
      captureRichFiles({
        fetchImpl,
        vault: "https://prints.example.com",
        authorization: "credential",
        sourceUrl: "https://www.printables.com/model/9",
        captureSource: {
          provider: "printables",
          canonical_url: "https://www.printables.com/model/9",
          source_item_id: "9",
          source_revision: null,
          adapter_version: "browser-visible-v1",
          tags: [],
          fields: {},
        },
        files: [
          { id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" },
        ],
      }),
    ).rejects.toThrow(message);
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("rejects duplicate or unsafe source file IDs before network calls", async () => {
    for (const ids of [["same", "same"], ["unsafe id"]]) {
      const fetchImpl = vi.fn();
      await expect(
        captureRichFiles({
          fetchImpl,
          vault: "https://prints.example.com",
          authorization: "credential",
          sourceUrl: "https://www.printables.com/model/9",
          title: "Cube",
          captureSource: {
            provider: "printables",
            canonical_url: "https://www.printables.com/model/9",
            source_item_id: "9",
            source_revision: null,
            adapter_version: "browser-visible-v1",
            tags: [],
            fields: {},
          },
          files: ids.map((id) => ({
            id,
            file: new Blob(["x"]),
            filename: "cube.3mf",
            mediaType: "model/3mf",
          })),
        }),
      ).rejects.toThrow("Capture file IDs");
      expect(fetchImpl).not.toHaveBeenCalled();
    }
  });

  it("rejects oversized individual and aggregate payloads before creating slots", async () => {
    const source = {
      provider: "makerworld" as const,
      canonical_url: "https://makerworld.com/en/models/9-cube",
      source_item_id: "9",
      source_revision: null,
      adapter_version: "makerworld-design-service-v1",
      tags: [],
      fields: {},
    };
    const oversized = new Blob(["x"]);
    Object.defineProperty(oversized, "size", { value: CAPTURE_MAX_FILE_SIZE_BYTES + 1 });
    const fetchImpl = vi.fn();
    await expect(
      captureRichFiles({
        fetchImpl,
        vault: "https://prints.example.com",
        authorization: "credential",
        sourceUrl: source.canonical_url,
        captureSource: source,
        files: [{ id: "large", file: oversized, filename: "large.3mf", mediaType: "model/3mf" }],
      }),
    ).rejects.toThrow("supported size limit");
    expect(fetchImpl).not.toHaveBeenCalled();

    const first = new Blob(["a"]);
    const second = new Blob(["b"]);
    const third = new Blob(["c"]);
    Object.defineProperty(first, "size", { value: CAPTURE_MAX_FILE_SIZE_BYTES });
    Object.defineProperty(second, "size", { value: CAPTURE_MAX_FILE_SIZE_BYTES });
    Object.defineProperty(third, "size", { value: 1 });
    await expect(
      captureRichFiles({
        fetchImpl,
        vault: "https://prints.example.com",
        authorization: "credential",
        sourceUrl: source.canonical_url,
        captureSource: source,
        files: [
          { id: "first", file: first, filename: "first.3mf", mediaType: "model/3mf" },
          { id: "second", file: second, filename: "second.3mf", mediaType: "model/3mf" },
          { id: "third", file: third, filename: "third.3mf", mediaType: "model/3mf" },
        ],
      }),
    ).rejects.toThrow("aggregate size limit");
    expect(CAPTURE_MAX_TOTAL_SIZE_BYTES).toBe(CAPTURE_MAX_FILE_SIZE_BYTES * 2);
    expect(fetchImpl).not.toHaveBeenCalled();
  });
  it("creates slots, uploads each selected browser file, and finalizes before returning an importable item", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          item: { id: 44 },
          slots: [
            {
              id: "slot-a",
              role: "file",
              source_file_id: "44:cube.3mf",
              filename: "cube.3mf",
              media_type: "model/3mf",
              size_bytes: 4,
              sha256: "d30ca7a7a32bf5772dc5eb2a2e7bd35737eff795ad74f2479b359716b59abdfa",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ id: 44, state: "ready" }));

    const result = await captureRichFiles({
      fetchImpl,
      vault: "https://prints.example.com",
      authorization: "device-secret",
      sourceUrl: "https://www.printables.com/model/44-calibration-cube",
      title: "Calibration cube",
      captureSource: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/44-calibration-cube",
        source_item_id: "44",
        source_revision: null,
        adapter_version: "browser-visible-v1",
        tags: ["calibration"],
        fields: {},
      },
      files: [
        {
          id: "44:cube.3mf",
          file: new Blob(["mesh"]),
          filename: "cube.3mf",
          mediaType: "model/3mf",
        },
      ],
    });

    expect(result).toEqual({ id: 44, state: "ready" });
    expect(fetchImpl).toHaveBeenNthCalledWith(
      1,
      "https://prints.example.com/api/v1/inbox/capture-upload-slots",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ Authorization: "Bearer device-secret" }),
      }),
    );
    expect(JSON.parse(fetchImpl.mock.calls[0][1].body)).toMatchObject({
      source_url: "https://www.printables.com/model/44-calibration-cube",
      capture_source: { provider: "printables" },
      files: [
        {
          id: "44:cube.3mf",
          filename: "cube.3mf",
          media_type: "model/3mf",
          size_bytes: 4,
          sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
        },
      ],
    });
    expect(fetchImpl).toHaveBeenNthCalledWith(
      2,
      "https://prints.example.com/api/v1/inbox/capture-upload-slots/slot-a",
      expect.objectContaining({ method: "PUT" }),
    );
    expect(fetchImpl).toHaveBeenNthCalledWith(
      3,
      "https://prints.example.com/api/v1/inbox/44/capture-upload-finalize",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("preserves provider IDs when selected files are reordered and slots arrive out of order", async () => {
    const fetchImpl = vi
      .fn()
      .mockResolvedValueOnce(
        Response.json({
          item: { id: 45 },
          slots: [
            {
              id: "slot-a",
              role: "file",
              source_file_id: "45:a.stl",
              filename: "a.stl",
              media_type: "model/stl",
              size_bytes: 1,
              sha256: "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb",
            },
            {
              id: "slot-b",
              role: "file",
              source_file_id: "45:b.stl",
              filename: "b.stl",
              media_type: "model/stl",
              size_bytes: 2,
              sha256: "3b64db95cb55c763391c707108489ae18b4112d783300de38e033b4c98c3deaf",
            },
          ],
        }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(Response.json({ id: 45, state: "ready" }));

    await captureRichFiles({
      fetchImpl,
      vault: "https://prints.example.com",
      authorization: "device-secret",
      sourceUrl: "https://www.printables.com/model/45-parts",
      captureSource: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/45-parts",
        source_item_id: "45",
        source_revision: null,
        adapter_version: "browser-visible-v1",
        tags: [],
        fields: {},
      },
      files: [
        { id: "45:b.stl", file: new Blob(["bb"]), filename: "b.stl", mediaType: "model/stl" },
        { id: "45:a.stl", file: new Blob(["a"]), filename: "a.stl", mediaType: "model/stl" },
      ],
    });

    const createOptions = fetchImpl.mock.calls[0]?.[1];
    if (createOptions === undefined || typeof createOptions.body !== "string") {
      throw new Error("Missing slot create body");
    }
    expect(JSON.parse(createOptions.body).files.map((file: { id: string }) => file.id)).toEqual([
      "45:b.stl",
      "45:a.stl",
    ]);
    expect(fetchImpl.mock.calls[1]?.[0]).toContain("slot-b");
    expect(fetchImpl.mock.calls[2]?.[0]).toContain("slot-a");
  });

  it("aborts slot creation on timeout without progressing to upload or finalize", async () => {
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
    const runStage: CaptureStageRunner = async (_stage, operation) => {
      const controller = new AbortController();
      const pending = operation(controller.signal);
      setTimeout(() => controller.abort(), 0);
      return pending;
    };
    await expect(
      captureRichFiles({
        fetchImpl,
        vault: "https://prints.example.com",
        authorization: "device-secret",
        sourceUrl: "https://www.printables.com/model/44-calibration-cube",
        captureSource: {
          provider: "printables",
          canonical_url: "https://www.printables.com/model/44-calibration-cube",
          source_item_id: "44",
          source_revision: null,
          adapter_version: "browser-visible-v1",
          tags: [],
          fields: {},
        },
        files: [
          {
            id: "44:cube.3mf",
            file: new Blob(["mesh"]),
            filename: "cube.3mf",
            mediaType: "model/3mf",
          },
        ],
        runStage,
      }),
    ).rejects.toThrow("aborted");
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]?.[1]?.signal?.aborted).toBe(true);
  });

  it("dismisses only the original capture when a held upload is retired", async () => {
    const controller = new AbortController();
    let entered: () => void = () => {};
    const uploading = new Promise<void>((resolve) => {
      entered = resolve;
    });
    const fetchImpl = vi.fn<typeof fetch>(async (input, options = {}) => {
      const url = String(input);
      if (url.endsWith("/capture-upload-slots"))
        return Response.json({
          item: { id: 44 },
          slots: [
            {
              id: "slot-a",
              role: "file",
              source_file_id: "part",
              filename: "part.stl",
              media_type: "model/stl",
              size_bytes: 1,
              sha256: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
            },
          ],
        });
      if (options.method === "PUT") {
        entered();
        return new Promise<Response>(() => {});
      }
      if (options.method === "DELETE") return new Response(null, { status: 204 });
      throw new Error(`Unexpected request ${url}`);
    });
    const outcome = captureRichFiles({
      fetchImpl,
      vault: "https://vault-a.example.com",
      authorization: "credential-a",
      signal: controller.signal,
      sourceUrl: "https://www.printables.com/model/9",
      captureSource: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/9",
        source_item_id: "9",
        source_revision: null,
        adapter_version: "browser-visible-v1",
        tags: [],
        fields: {},
      },
      files: [{ id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" }],
    });
    const rejected = expect(outcome).rejects.toThrow("aborted");
    await uploading;
    controller.abort();
    await rejected;
    const deletion = fetchImpl.mock.calls.filter(([, options]) => options?.method === "DELETE");
    expect(deletion).toHaveLength(1);
    expect(deletion[0]).toEqual([
      "https://vault-a.example.com/api/v1/inbox/44/capture-upload",
      expect.objectContaining({ headers: { Authorization: "Bearer credential-a" } }),
    ]);
    expect(deletion[0]?.[1]?.signal?.aborted).toBe(false);
    expect(fetchImpl.mock.calls.some(([input]) => String(input).includes("finalize"))).toBe(false);
  });

  it("bounds dismissal without replacing the original upload failure", async () => {
    vi.useFakeTimers();
    try {
      let entered: () => void = () => {};
      const cleaning = new Promise<void>((resolve) => {
        entered = resolve;
      });
      const fetchImpl = vi.fn<typeof fetch>(async (input, options = {}) => {
        if (String(input).endsWith("/capture-upload-slots"))
          return Response.json({
            item: { id: 44 },
            slots: [
              {
                id: "slot-a",
                role: "file",
                source_file_id: "part",
                filename: "part.stl",
                media_type: "model/stl",
                size_bytes: 1,
                sha256: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
              },
            ],
          });
        if (options.method === "PUT") return new Response(null, { status: 503 });
        if (options.method === "DELETE") {
          entered();
          return new Promise<Response>(() => {});
        }
        throw new Error("Unexpected request");
      });
      const outcome = captureRichFiles({
        fetchImpl,
        vault: "https://vault-a.example.com",
        authorization: "credential-a",
        sourceUrl: "https://www.printables.com/model/9",
        captureSource: {
          provider: "printables",
          canonical_url: "https://www.printables.com/model/9",
          source_item_id: "9",
          source_revision: null,
          adapter_version: "browser-visible-v1",
          tags: [],
          fields: {},
        },
        files: [
          { id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" },
        ],
      });
      const rejected = expect(outcome).rejects.toThrow("while uploading part.stl");
      await cleaning;
      await vi.advanceTimersByTimeAsync(CAPTURE_CLEANUP_TIMEOUT_MS);
      await rejected;
      const deletion = fetchImpl.mock.calls.find(([, options]) => options?.method === "DELETE");
      expect(deletion?.[1]?.signal?.aborted).toBe(true);
      expect(fetchImpl).toHaveBeenCalledTimes(3);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("rich capture authentication boundary", () => {
  const stages = ["slot_create", "slot_upload", "slot_finalize"] as const;
  function attempt(rejectedStage: (typeof stages)[number], status: number, cleanupStatus = 204) {
    const requests: { path: string; method: string; authorization: string | null }[] = [];
    const outcome = captureRichFiles({
      vault: "https://vault-a.example.com",
      authorization: "test-credential-a",
      sourceUrl: "https://www.printables.com/model/9",
      captureSource: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/9",
        source_item_id: "9",
        source_revision: null,
        adapter_version: "browser-visible-v1",
        tags: [],
        fields: {},
      },
      files: [{ id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" }],
      fetchImpl: async (input, options = {}) => {
        const path = String(input);
        requests.push({
          path,
          method: options.method ?? "GET",
          authorization: new Headers(options.headers).get("Authorization"),
        });
        if (options.method === "DELETE") return new Response(null, { status: cleanupStatus });
        const stage = path.endsWith("/capture-upload-slots")
          ? "slot_create"
          : options.method === "PUT"
            ? "slot_upload"
            : "slot_finalize";
        if (stage === rejectedStage)
          return Response.json(
            {
              detail: status === 401 ? "invalid_browser_credential" : "insufficient_scope",
              secret: "test-secret",
            },
            { status },
          );
        if (stage === "slot_create")
          return Response.json({
            item: { id: 44 },
            slots: [
              {
                id: "slot-a",
                role: "file",
                source_file_id: "part",
                filename: "part.stl",
                media_type: "model/stl",
                size_bytes: 1,
                sha256: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
              },
            ],
          });
        return new Response(null, { status: 204 });
      },
    });
    return { outcome, requests };
  }

  it.each(stages)("rejects rich capture authentication failures: %s", async (stage) => {
    const { outcome, requests } = attempt(stage, 401);
    await expect(outcome).rejects.toMatchObject({
      name: "CaptureAuthenticationError",
      message: "The PrintStash connection expired. Reconnect and try again.",
    });
    expect(
      requests.filter((request) => request.method !== "DELETE").map((request) => request.method),
    ).toEqual(
      stage === "slot_create"
        ? ["POST"]
        : stage === "slot_upload"
          ? ["POST", "PUT"]
          : ["POST", "PUT", "POST"],
    );
  });

  it.each(stages)("keeps rich capture permission failures scoped: %s", async (stage) => {
    await expect(attempt(stage, 403).outcome).rejects.toMatchObject({
      name: "Error",
      message: expect.stringContaining("403"),
    });
  });

  it("preserves authentication failure through owned cleanup", async () => {
    const { outcome, requests } = attempt("slot_upload", 401, 403);
    await expect(outcome).rejects.toMatchObject({ name: "CaptureAuthenticationError" });
    expect(requests.filter((request) => request.method === "DELETE")).toEqual([
      {
        path: "https://vault-a.example.com/api/v1/inbox/44/capture-upload",
        method: "DELETE",
        authorization: "Bearer test-credential-a",
      },
    ]);
  });
});

describe("capture acknowledgement ownership", () => {
  const validSlot = {
    id: "slot-a",
    role: "file",
    source_file_id: "part",
    filename: "part.stl",
    media_type: "model/stl",
    size_bytes: 1,
    sha256: "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881",
  };
  const invalidSlots = "PrintStash returned invalid capture upload slots.";

  function captureWithAcknowledgement(
    acknowledgement: Response | Promise<Response>,
    options: {
      signal?: AbortSignal;
      cleanup?: (signal: AbortSignal | null | undefined) => Promise<Response>;
      cover?: boolean;
    } = {},
  ) {
    const requests: { url: string; method: string; authorization: string | null }[] = [];
    const outcome = captureRichFiles({
      vault: "https://vault-a.example.com",
      authorization: "test-credential-a",
      sourceUrl: "https://www.printables.com/model/9",
      captureSource: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/9",
        source_item_id: "9",
        source_revision: null,
        adapter_version: "browser-visible-v1",
        tags: [],
        fields: {},
      },
      files: [{ id: "part", file: new Blob(["x"]), filename: "part.stl", mediaType: "model/stl" }],
      ...(options.cover
        ? {
            cover: {
              id: "cover",
              file: new Blob(["x"]),
              filename: "cover.png",
              mediaType: "image/png",
            },
          }
        : {}),
      signal: options.signal,
      fetchImpl: async (input, init = {}) => {
        const url = String(input);
        requests.push({
          url,
          method: init.method ?? "GET",
          authorization: new Headers(init.headers).get("Authorization"),
        });
        if (url.endsWith("/capture-upload-slots")) return acknowledgement;
        if (init.method === "DELETE")
          return options.cleanup
            ? options.cleanup(init.signal)
            : new Response(null, { status: 204 });
        if (init.method === "PUT") return new Response(null, { status: 204 });
        if (url.endsWith("/capture-upload-finalize"))
          return Response.json({ id: 44, state: "review" });
        throw new Error("Unexpected capture request");
      },
    });
    return { outcome, requests };
  }

  it.each([
    { label: "missing", body: { item: { id: 44 } } },
    { label: "null", body: { item: { id: 44 }, slots: null } },
    { label: "object", body: { item: { id: 44 }, slots: {} } },
    { label: "too few", body: { item: { id: 44 }, slots: [] } },
    { label: "too many", body: { item: { id: 44 }, slots: [validSlot, validSlot] } },
  ])("dismisses owned receipts with malformed slot lists: $label", async ({ body }) => {
    const { outcome, requests } = captureWithAcknowledgement(Response.json(body));
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.map(({ method }) => method)).toEqual(["POST", "DELETE"]);
    expect(requests[1]?.url).toBe("https://vault-a.example.com/api/v1/inbox/44/capture-upload");
  });

  it.each([
    { label: "null", slot: null },
    { label: "primitive", slot: 7 },
    { label: "missing id", slot: { ...validSlot, id: undefined } },
    { label: "empty id", slot: { ...validSlot, id: "" } },
    { label: "blank id", slot: { ...validSlot, id: " " } },
    { label: "unknown role", slot: { ...validSlot, role: "thumbnail" } },
    { label: "wrong source id", slot: { ...validSlot, source_file_id: "other" } },
    { label: "wrong filename", slot: { ...validSlot, filename: "other.stl" } },
    { label: "wrong media type", slot: { ...validSlot, media_type: "other" } },
    { label: "wrong size", slot: { ...validSlot, size_bytes: 2 } },
    { label: "wrong hash", slot: { ...validSlot, sha256: "other" } },
  ])("dismisses owned receipts with malformed slot members: $label", async ({ slot }) => {
    const { outcome, requests } = captureWithAcknowledgement(
      Response.json({ item: { id: 44 }, slots: [slot] }),
    );
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.map(({ method }) => method)).toEqual(["POST", "DELETE"]);
  });

  it.each([
    { label: "null payload", body: null },
    { label: "missing item", body: { slots: [validSlot] } },
    { label: "null item", body: { item: null, slots: [validSlot] } },
    { label: "missing id", body: { item: {}, slots: [validSlot] } },
    ...["44", null, 0, -1, 1.5, Number.MAX_SAFE_INTEGER + 1].map((id) => ({
      label: `id ${id}`,
      body: { item: { id }, slots: [validSlot] },
    })),
  ])("rejects uncertain receipts without dismissal: $label", async ({ body }) => {
    const { outcome, requests } = captureWithAcknowledgement(Response.json(body));
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.map(({ method }) => method)).toEqual(["POST"]);
  });

  it("rejects unreadable acknowledgement bodies without dismissal", async () => {
    const { outcome, requests } = captureWithAcknowledgement(new Response("unreadable JSON"));
    await expect(outcome).rejects.toBeInstanceOf(SyntaxError);
    expect(requests.map(({ method }) => method)).toEqual(["POST"]);
  });

  it.each([
    { label: "forbidden", cleanup: async () => new Response(null, { status: 403 }) },
    { label: "server failure", cleanup: async () => new Response(null, { status: 500 }) },
    {
      label: "network failure",
      cleanup: async () => {
        throw new Error("Cleanup network failure");
      },
    },
  ])("preserves validation failure when cleanup fails: $label", async ({ cleanup }) => {
    const { outcome, requests } = captureWithAcknowledgement(
      Response.json({ item: { id: 44 }, slots: [] }),
      { cleanup },
    );
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.map(({ method }) => method)).toEqual(["POST", "DELETE"]);
  });

  it("bounds malformed-ACK dismissal", async () => {
    vi.useFakeTimers();
    try {
      let cleanupEntered!: () => void;
      const entered = new Promise<void>((resolve) => {
        cleanupEntered = resolve;
      });
      let cleanupSignal: AbortSignal | null | undefined;
      const { outcome } = captureWithAcknowledgement(
        Response.json({ item: { id: 44 }, slots: [] }),
        {
          cleanup: (signal) => {
            cleanupSignal = signal;
            cleanupEntered();
            return new Promise<Response>(() => {});
          },
        },
      );
      const rejected = expect(outcome).rejects.toThrow(invalidSlots);
      // Waiting for cleanup with a bounded expectation keeps the red run from
      // hanging when the original implementation fails before starting it.
      await vi.waitFor(() => expect(cleanupSignal).toBeDefined());
      await entered;
      await vi.advanceTimersByTimeAsync(CAPTURE_CLEANUP_TIMEOUT_MS);
      await rejected;
      expect(cleanupSignal?.aborted).toBe(true);
    } finally {
      vi.useRealTimers();
    }
  });

  it("binds malformed-ACK dismissal to its original connection", async () => {
    const { outcome, requests } = captureWithAcknowledgement(
      Response.json({ item: { id: 44 }, slots: [] }),
    );
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.filter(({ method }) => method === "DELETE")).toEqual([
      {
        url: "https://vault-a.example.com/api/v1/inbox/44/capture-upload",
        method: "DELETE",
        authorization: "Bearer test-credential-a",
      },
    ]);
  });

  it.each(["headers", "body"])(
    "ignores late creation acknowledgements after retirement: %s",
    async (phase) => {
      const controller = new AbortController();
      let releaseHeaders!: (response: Response) => void;
      const headers = new Promise<Response>((resolve) => {
        releaseHeaders = resolve;
      });
      let body!: ReadableStreamDefaultController<Uint8Array>;
      const stream = new ReadableStream<Uint8Array>({
        start(value) {
          body = value;
        },
      });
      const { outcome, requests } = captureWithAcknowledgement(
        phase === "headers" ? headers : new Response(stream),
        { signal: controller.signal },
      );
      const rejected = expect(outcome).rejects.toThrow("aborted");
      await vi.waitFor(() => expect(requests).toHaveLength(1));
      controller.abort();
      await rejected;
      const payload = { item: { id: 44 }, slots: [validSlot] };
      if (phase === "headers") releaseHeaders(Response.json(payload));
      else {
        body.enqueue(new TextEncoder().encode(JSON.stringify(payload)));
        body.close();
      }
      await new Promise((resolve) => setTimeout(resolve, 0));
      expect(requests.map(({ method }) => method)).toEqual(["POST"]);
    },
  );

  it("accepts a mixed file and cover acknowledgement", async () => {
    const { outcome, requests } = captureWithAcknowledgement(
      Response.json({
        item: { id: 44 },
        slots: [
          {
            ...validSlot,
            id: "cover-a",
            role: "cover",
            source_file_id: null,
            filename: "cover.png",
            media_type: "image/png",
          },
          validSlot,
        ],
      }),
      { cover: true },
    );
    await expect(outcome).resolves.toEqual({ id: 44, state: "review" });
    expect(requests.filter(({ method }) => method === "PUT").map(({ url }) => url)).toEqual([
      "https://vault-a.example.com/api/v1/inbox/capture-upload-slots/slot-a",
      "https://vault-a.example.com/api/v1/inbox/capture-upload-slots/cover-a",
    ]);
  });

  it("rejects reused upload slot identities", async () => {
    const { outcome, requests } = captureWithAcknowledgement(
      Response.json({
        item: { id: 44 },
        slots: [
          validSlot,
          {
            ...validSlot,
            role: "cover",
            source_file_id: null,
            filename: "cover.png",
            media_type: "image/png",
          },
        ],
      }),
      { cover: true },
    );
    await expect(outcome).rejects.toThrow(invalidSlots);
    expect(requests.map(({ method }) => method)).toEqual(["POST", "DELETE"]);
  });
});
