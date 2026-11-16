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

describe("recoverable capture options", () => {
  it("sends review suggestions only when negotiated", async () => {
    const declarations: unknown[] = [];
    const saved: Record<string, unknown> = {};
    await captureSequentialFiles({
      vault: "https://vault.test",
      authorization: "test-device",
      source,
      collectionId: 7,
      tags: ["assembly"],
      candidates: [{ id: "a", filename: "a.stl", fileType: "stl" }],
      storage: {
        get: async () => saved,
        set: async (values) => {
          Object.assign(saved, values);
        },
        remove: async () => {},
      },
      progress: () => {},
      download: async () => {
        throw new Error("Finalized capture must not download again");
      },
      fetchImpl: async (url, init) => {
        if (String(url).endsWith("/capture-capabilities"))
          return Response.json({ resumable_slots: true, capture_preferences: true });
        declarations.push(JSON.parse(String(init?.body)));
        return Response.json({ item: { id: 3, state: "review" }, slots: [], recoverable: false });
      },
    });
    expect(declarations).toEqual([
      expect.objectContaining({ collection_id: 7, tags: ["assembly"] }),
    ]);
  });

  it.each(["pending", "uploaded"] as const)(
    "recovers a %s cover without repeating model downloads",
    async (state) => {
      const file = new Blob(["png"]);
      const sha256 = [
        ...new Uint8Array(await crypto.subtle.digest("SHA-256", await file.arrayBuffer())),
      ]
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
      const uploaded: Blob[] = [];
      const phases: string[] = [];
      const result = await captureSequentialFiles({
        vault: "https://vault.test",
        authorization: "test-device",
        source,
        ...(state === "pending"
          ? { cover: { id: "cover", filename: "cover.png", mediaType: "image/png", file } }
          : {}),
        candidates: [{ id: "a", filename: "a.stl", fileType: "stl" }],
        storage: { get: async () => ({}), set: async () => {}, remove: async () => {} },
        progress: (message) => phases.push(message),
        download: async () => {
          throw new Error("Confirmed model must not download again");
        },
        fetchImpl: async (url, init) => {
          if (String(url).endsWith("/capture-capabilities"))
            return Response.json({ resumable_slots: true, resumable_cover: true });
          if (String(url).endsWith("/capture-sessions"))
            return Response.json({
              item: { id: 3, state: "captured" },
              recoverable: true,
              slots: [
                { source_file_id: "a", size_bytes: 1, state: "uploaded" },
                {
                  id: "cover-slot",
                  role: "cover",
                  source_file_id: null,
                  filename: "cover.png",
                  media_type: "image/png",
                  size_bytes: file.size,
                  sha256,
                  state,
                },
              ],
            });
          if (String(url).endsWith("/capture-upload-slots/cover-slot")) {
            if (!(init?.body instanceof Blob)) throw new Error("Expected cover bytes");
            uploaded.push(init.body);
            return Response.json({});
          }
          if (String(url).endsWith("/capture-upload-finalize")) return Response.json({});
          throw new Error("Unexpected request");
        },
      });
      expect(result.id).toBe(3);
      expect(uploaded).toEqual(state === "pending" ? [file] : []);
      expect(phases.at(-1)).toBe("Transfer complete. Preparing review…");
    },
  );

  it.each(["missing file", "different file", "invalid receipt"])(
    "preserves the capture when cover recovery has a %s",
    async (problem) => {
      const saved: Record<string, unknown> = {};
      const requests: string[] = [];
      const operation = captureSequentialFiles({
        vault: "https://vault.test",
        authorization: "test-device",
        source,
        ...(problem === "different file"
          ? {
              cover: {
                id: "cover",
                filename: "new.png",
                mediaType: "image/png",
                file: new Blob(["new"]),
              },
            }
          : {}),
        candidates: [{ id: "a", filename: "a.stl", fileType: "stl" }],
        storage: {
          get: async () => saved,
          set: async (values) => {
            Object.assign(saved, values);
          },
          remove: async () => {
            throw new Error("Must preserve recovery identity");
          },
        },
        progress: () => {},
        download: async () => {
          throw new Error("Must require original cover before downloading");
        },
        fetchImpl: async (url) => {
          requests.push(String(url));
          if (String(url).endsWith("/capture-capabilities"))
            return Response.json({ resumable_slots: true, resumable_cover: true });
          return Response.json({
            item: { id: 3, state: "captured" },
            recoverable: true,
            slots: [
              {
                id: "cover-slot",
                role: "cover",
                source_file_id: null,
                filename: "cover.png",
                media_type: "image/png",
                size_bytes: 3,
                sha256: problem === "invalid receipt" ? "invalid" : "a".repeat(64),
                state: "pending",
              },
            ],
          });
        },
      });
      await expect(operation).rejects.toThrow(
        problem === "missing file"
          ? "Reselect the original cover"
          : problem === "different file"
            ? "does not match"
            : "invalid cover receipt",
      );
      expect(requests).toHaveLength(2);
      expect(Object.keys(saved)).toHaveLength(1);
    },
  );

  it.each(["cover", "collection"])(
    "refuses unsupported %s options before downloading",
    async (option) => {
      await expect(
        captureSequentialFiles({
          vault: "https://vault.test",
          authorization: "test-device",
          source,
          ...(option === "cover"
            ? {
                cover: {
                  id: "cover",
                  filename: "cover.png",
                  mediaType: "image/png",
                  file: new Blob(["png"]),
                },
              }
            : { collectionId: 7 }),
          candidates: [{ id: "a", filename: "a.stl", fileType: "stl" }],
          storage: {
            get: async () => ({}),
            set: async () => {
              throw new Error("Must not create a request");
            },
            remove: async () => {},
          },
          progress: () => {},
          download: async () => {
            throw new Error("Must not download");
          },
          fetchImpl: async () => Response.json({ resumable_slots: true }),
        }),
      ).rejects.toThrow("Update PrintStash");
    },
  );
});
