/** Uploads reach PrintStash through proxies that cap one request's size. */
import { beforeEach, describe, expect, it } from "vitest";
import {
  FIRST_PART_BYTES,
  forgetLearnedPartSizes,
  SMALLEST_PART_BYTES,
  uploadSlotBytes,
  UploadTooLargeError,
} from "../slot-upload.ts";

const MiB = 1024 * 1024;

function json(body: unknown, status = 200) {
  return Response.json(body, { status });
}

/** A PrintStash slot behind a proxy that refuses bodies over `limit` bytes. */
function proxiedServer({ limit = Infinity, size }: { limit?: number; size: number }) {
  const stored: number[] = [];
  const requests: Array<{ path: string; bytes: number }> = [];
  let held = 0;
  const send = async (path: string, init: RequestInit) => {
    const body = init.body;
    const bytes =
      body instanceof Blob ? body.size : body instanceof ArrayBuffer ? body.byteLength : 0;
    requests.push({ path, bytes });
    if (bytes > limit)
      return new Response("<html>413 Request Entity Too Large</html>", { status: 413 });
    const part = /\/parts\/(\d+)$/.exec(path);
    if (part) {
      const offset = Number(part[1]);
      if (offset > held)
        return json({ detail: "capture_upload_offset_mismatch", received_bytes: held }, 409);
      held = offset + bytes;
      stored.push(bytes);
      return json({ received_bytes: held });
    }
    if (path.endsWith("/complete"))
      return held === size
        ? json({ state: "uploaded" })
        : json({ detail: "capture_upload_size_mismatch" }, 400);
    held = bytes;
    return json({ state: "uploaded" });
  };
  return { send, stored, requests };
}

const upload = (
  server: ReturnType<typeof proxiedServer>,
  file: Blob,
  extra: Partial<Parameters<typeof uploadSlotBytes>[0]> = {},
) =>
  uploadSlotBytes({
    send: server.send,
    server: "https://prints.example.com",
    slotId: "slot-1",
    file,
    mediaType: "model/3mf",
    parts: true,
    ...extra,
  });

describe("slot uploads", () => {
  beforeEach(forgetLearnedPartSizes);

  it("sends a file that fits one request whole", async () => {
    const server = proxiedServer({ size: 1000 });
    const result = await upload(server, new Blob([new Uint8Array(1000)]));
    expect(result.ok).toBe(true);
    expect(server.requests.map((request) => request.path)).toEqual([
      "/capture-upload-slots/slot-1",
    ]);
  });

  it("sends a large file in parts, then completes it", async () => {
    const size = 2 * FIRST_PART_BYTES + 5;
    const server = proxiedServer({ size });
    const progress: number[] = [];
    const result = await upload(server, new Blob([new Uint8Array(size)]), {
      progress: (loaded) => progress.push(loaded),
    });
    expect(result.ok).toBe(true);
    expect(server.stored).toEqual([FIRST_PART_BYTES, FIRST_PART_BYTES, 5]);
    expect(server.requests.at(-1)?.path).toBe("/capture-upload-slots/slot-1/complete");
    expect(progress).toEqual([FIRST_PART_BYTES, 2 * FIRST_PART_BYTES, size]);
  });

  it("halves the part size under a proxy limit and keeps what worked", async () => {
    const size = 5 * MiB;
    const server = proxiedServer({ limit: 1 * MiB, size });
    expect((await upload(server, new Blob([new Uint8Array(size)]))).ok).toBe(true);
    expect(Math.max(...server.stored)).toBeLessThanOrEqual(1 * MiB);

    // The next file starts at the size this proxy accepted.
    const next = proxiedServer({ limit: 1 * MiB, size });
    await upload(next, new Blob([new Uint8Array(size)]));
    expect(next.requests.every((request) => request.bytes <= 1 * MiB)).toBe(true);
  });

  it("returns PrintStash's own size refusal instead of splitting the file", async () => {
    const send = async () => json({ detail: "upload_too_large" }, 413);
    const result = await uploadSlotBytes({
      send,
      server: "https://prints.example.com",
      slotId: "slot-1",
      file: new Blob([new Uint8Array(10)]),
      mediaType: "model/3mf",
      parts: true,
    });
    expect(result.status).toBe(413);
  });

  it("continues from the bytes the server reports holding", async () => {
    const size = FIRST_PART_BYTES + 10;
    const server = proxiedServer({ size });
    let first = true;
    const send = async (path: string, init: RequestInit) => {
      // The first part's response is lost; the server never stored it.
      if (first && path.endsWith("/parts/0")) {
        first = false;
        return json({ detail: "capture_upload_offset_mismatch", received_bytes: 0 }, 409);
      }
      return server.send(path, init);
    };
    const result = await upload({ ...server, send }, new Blob([new Uint8Array(size)]));
    expect(result.ok).toBe(true);
    expect(server.stored).toEqual([FIRST_PART_BYTES, 10]);
  });

  it("explains a proxy that refuses even the smallest part", async () => {
    const server = proxiedServer({ limit: SMALLEST_PART_BYTES - 1, size: MiB });
    await expect(upload(server, new Blob([new Uint8Array(MiB)]))).rejects.toThrow(
      /client_max_body_size/,
    );
  });

  it("names the proxy limit to servers that cannot take parts", async () => {
    const server = proxiedServer({ limit: 10, size: 100 });
    const failure = await upload(server, new Blob([new Uint8Array(100)]), { parts: false }).catch(
      (error) => error,
    );
    expect(failure).toBeInstanceOf(UploadTooLargeError);
    expect(failure.message).toContain("Update PrintStash");
  });

  it("refuses to complete when the server stored a different count", async () => {
    const size = FIRST_PART_BYTES + 1;
    const send = async (path: string) =>
      path.includes("/parts/") ? json({ received_bytes: 3 }) : json({ state: "uploaded" });
    await expect(
      uploadSlotBytes({
        send,
        server: "https://prints.example.com",
        slotId: "slot-1",
        file: new Blob([new Uint8Array(size)]),
        mediaType: "model/3mf",
        parts: true,
      }),
    ).rejects.toThrow("different number of bytes");
  });
});
