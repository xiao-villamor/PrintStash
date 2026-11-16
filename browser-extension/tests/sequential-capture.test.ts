/** Recovery retains verified source files and rejects mismatched upload receipts. */
import { describe, expect, it } from "vitest";
import { captureSequentialFiles } from "../sequential-capture.ts";
import type { CaptureSourceDraft } from "../capture-adapter.ts";

const source: CaptureSourceDraft = {
  provider: "makerworld",
  canonical_url: "https://makerworld.com/en/models/1234-widget",
  source_item_id: "1234",
  source_revision: null,
  adapter_version: "test-v1",
  tags: [],
  fields: {},
};

describe("captureSequentialFiles", () => {
  it("retains confirmed files after interruption and resolves only missing downloads on recovery", async () => {
    const saved: Record<string, unknown> = {};
    const downloaded: string[] = [];
    const events: string[] = [];
    let requestKey: string | null = null;
    let uploadedA = false;
    let interruptB = true;
    const storage = {
      get: async () => ({ ...saved }),
      set: async (values: Record<string, unknown>) => {
        Object.assign(saved, values);
      },
      remove: async (keys: string | string[]) => {
        for (const key of [keys].flat()) delete saved[key];
      },
    };
    const fetchImpl: typeof fetch = async (input, init) => {
      const url = String(input);
      if (url.endsWith("/capture-capabilities")) return Response.json({ resumable_slots: true });
      if (url.endsWith("/capture-sessions")) {
        const body = JSON.parse(String(init?.body));
        if (requestKey !== null) expect(body.request_key).toBe(requestKey);
        requestKey = body.request_key;
        return Response.json({
          item: { id: 9, state: "captured" },
          recoverable: true,
          slots: uploadedA ? [{ source_file_id: "a", state: "uploaded", size_bytes: 1 }] : [],
        });
      }
      if (url.endsWith("/capture-files")) {
        const d = JSON.parse(String(init?.body));
        return Response.json({ ...d, source_file_id: d.id });
      }
      if (url.endsWith("/capture-upload-slots/a")) {
        events.push("upload-a");
        uploadedA = true;
        return Response.json({});
      }
      if (url.endsWith("/capture-upload-slots/b")) {
        events.push("upload-b");
        if (interruptB) return new Response(null, { status: 503 });
        return Response.json({});
      }
      if (url.endsWith("/capture-upload-finalize"))
        return Response.json({ id: 9, state: "review" });
      throw new Error(`Unexpected request ${url}`);
    };
    const options = {
      vault: "https://vault.test",
      authorization: "test-credential",
      source,
      storage,
      fetchImpl,
      candidates: ["a", "b"].map((id) => ({ id, filename: `${id}.stl`, fileType: "stl" as const })),
      download: async (candidate: { id: string; filename: string }) => {
        downloaded.push(candidate.id);
        events.push(`download-${candidate.id}`);
        return {
          id: candidate.id,
          filename: candidate.filename,
          mediaType: "model/stl",
          file: new Blob([candidate.id]),
        };
      },
      progress: () => {},
    };
    await expect(captureSequentialFiles(options)).rejects.toThrow("Transfer interrupted");
    expect(events).toEqual(["download-a", "upload-a", "download-b", "upload-b"]);
    expect(Object.values(saved)).toEqual([requestKey]);
    expect(JSON.stringify(saved)).not.toContain("test-credential");
    interruptB = false;
    expect(await captureSequentialFiles(options)).toEqual({ id: 9, state: "captured" });
    expect(downloaded).toEqual(["a", "b", "b"]);
    expect(saved).toEqual({});
  });

  it("recovers a lost finalization response without downloading the file again", async () => {
    const saved: Record<string, unknown> = {};
    let finalized = false;
    let downloads = 0;
    const options = {
      vault: "https://vault.test",
      authorization: "device-test",
      source,
      candidates: [{ id: "one", filename: "one.stl", fileType: "stl" as const }],
      storage: {
        get: async () => ({ ...saved }),
        set: async (v: Record<string, unknown>) => {
          Object.assign(saved, v);
        },
        remove: async (v: string | string[]) => {
          for (const k of [v].flat()) delete saved[k];
        },
      },
      progress: () => {},
      download: async () => {
        downloads++;
        return {
          id: "one",
          filename: "one.stl",
          mediaType: "model/stl",
          file: new Blob(["solid test"]),
        };
      },
      fetchImpl: (async (url, init) => {
        const path = new URL(String(url)).pathname;
        if (path.endsWith("capture-capabilities")) return Response.json({ resumable_slots: true });
        if (path.endsWith("capture-sessions"))
          return Response.json({
            item: { id: 42, state: finalized ? "review" : "captured" },
            recoverable: !finalized,
            slots: [],
          });
        if (path.endsWith("capture-files")) {
          const declaration = JSON.parse(String(init?.body));
          return Response.json({ ...declaration, source_file_id: declaration.id });
        }
        if (path.endsWith("capture-upload-slots/one")) return Response.json({ state: "uploaded" });
        if (path.endsWith("capture-upload-finalize")) {
          finalized = true;
          throw new TypeError("Network interrupted");
        }
        throw new Error("Unexpected request");
      }) as typeof fetch,
    };
    await expect(captureSequentialFiles(options)).rejects.toThrow("Network interrupted");
    expect(Object.keys(saved)).toHaveLength(1);
    expect(await captureSequentialFiles(options)).toEqual({ id: 42, state: "review" });
    expect(downloads).toBe(1);
    expect(saved).toEqual({});
  });

  it("does not send bytes to a slot whose receipt describes different content", async () => {
    const requests: string[] = [];
    await expect(
      captureSequentialFiles({
        vault: "https://vault.test",
        authorization: "device-test",
        source,
        candidates: [{ id: "one", filename: "one.stl", fileType: "stl" }],
        storage: { get: async () => ({}), set: async () => {}, remove: async () => {} },
        progress: () => {},
        download: async () => ({
          id: "one",
          filename: "one.stl",
          mediaType: "model/stl",
          file: new Blob(["solid test"]),
        }),
        fetchImpl: async (url, init) => {
          const path = new URL(String(url)).pathname;
          requests.push(`${init?.method ?? "GET"} ${path}`);
          if (path.endsWith("capture-capabilities"))
            return Response.json({ resumable_slots: true });
          if (path.endsWith("capture-sessions"))
            return Response.json({
              item: { id: 42, state: "captured" },
              recoverable: true,
              slots: [],
            });
          if (path.endsWith("capture-files")) {
            const d = JSON.parse(String(init?.body));
            return Response.json({ ...d, source_file_id: d.id, sha256: "0".repeat(64) });
          }
          return Response.json({});
        },
      }),
    ).rejects.toThrow("invalid capture slot receipt");
    expect(requests.some((request) => request.startsWith("PUT"))).toBe(false);
    expect(requests.some((request) => request.startsWith("DELETE"))).toBe(false);
  });
});
