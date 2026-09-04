import { describe, expect, it, vi } from "vitest";

import {
  CAPTURE_MAX_FILE_SIZE_BYTES,
  CAPTURE_MAX_TOTAL_SIZE_BYTES,
  captureRichFiles,
  type CaptureStageRunner,
} from "../capture-transport.ts";

describe("capture upload-slot transport", () => {
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
});
