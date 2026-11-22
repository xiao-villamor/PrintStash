/**
 * Send one capture slot's bytes, in parts when the server accepts them.
 *
 * A reverse proxy in front of PrintStash often caps one request's body: nginx
 * at 1 MiB unless configured, Cloudflare at 100 MB. A part refused with 413
 * by something other than PrintStash is sent again at half the size, and the
 * size that worked is reused for the rest of the popup's life.
 */
import type { ByteProgress } from "./transfer-progress.ts";

export const FIRST_PART_BYTES = 8 * 1024 * 1024;
export const SMALLEST_PART_BYTES = 256 * 1024;
/** Restarts tolerated after the server reports fewer stored bytes than sent. */
const MAX_RESUMES = 3;

const learnedPartBytes = new Map<string, number>();

/** Tests start each case from the first part size. */
export function forgetLearnedPartSizes() {
  learnedPartBytes.clear();
}

export class UploadTooLargeError extends Error {
  constructor(parts: boolean) {
    super(
      parts
        ? "A proxy in front of PrintStash refuses even small uploads. Raise its request size limit (for example nginx client_max_body_size) or connect to PrintStash directly."
        : "A proxy in front of PrintStash refused this file as too large. Update PrintStash so the extension can send it in parts, raise the proxy's request size limit, or connect to PrintStash directly.",
    );
    this.name = "UploadTooLargeError";
  }
}

/** PrintStash refuses with a JSON detail; a proxy answers with its own page. */
export async function refusedByProxy(response: Response) {
  if (response.status !== 413) return false;
  const body = await response
    .clone()
    .json()
    .catch(() => null);
  return typeof body?.detail !== "string";
}

export async function uploadSlotBytes({
  send,
  server,
  slotId,
  file,
  mediaType,
  parts,
  progress,
}: {
  /** Sends a request relative to the server's `/api/v1/inbox`, with its credential. */
  send(path: string, init: RequestInit): Promise<Response>;
  /** Server address, so a learned part size applies only to its own proxy. */
  server: string;
  slotId: string;
  file: Blob;
  mediaType: string;
  /** The server advertises `upload_parts`. */
  parts: boolean;
  progress?: ByteProgress;
}): Promise<Response> {
  const slot = `/capture-upload-slots/${encodeURIComponent(slotId)}`;
  let size = learnedPartBytes.get(server) ?? FIRST_PART_BYTES;
  if (!parts || file.size <= size) {
    const whole = await send(slot, {
      method: "PUT",
      headers: { "Content-Type": mediaType },
      body: file,
    });
    if (!(await refusedByProxy(whole))) return whole;
    if (!parts) throw new UploadTooLargeError(false);
    size = Math.max(SMALLEST_PART_BYTES, Math.min(size, Math.ceil(file.size / 2)));
  }
  let offset = 0;
  let resumes = 0;
  while (offset < file.size) {
    const part = file.slice(offset, offset + size);
    // An ArrayBuffer body goes through fetch; progress is reported per part.
    const response = await send(`${slot}/parts/${offset}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: await part.arrayBuffer(),
    });
    if (await refusedByProxy(response)) {
      if (size <= SMALLEST_PART_BYTES) throw new UploadTooLargeError(true);
      size = Math.max(SMALLEST_PART_BYTES, Math.floor(size / 2));
      learnedPartBytes.set(server, size);
      continue;
    }
    const body = await response
      .clone()
      .json()
      .catch(() => null);
    if (response.status === 409 && body?.detail === "capture_upload_offset_mismatch") {
      const stored = body.received_bytes;
      if (
        !Number.isSafeInteger(stored) ||
        stored < 0 ||
        stored > file.size ||
        ++resumes > MAX_RESUMES
      )
        return response;
      offset = stored;
      continue;
    }
    if (!response.ok) return response;
    if (body?.received_bytes !== offset + part.size)
      throw new Error("PrintStash stored a different number of bytes than were sent.");
    offset = body.received_bytes;
    progress?.(offset, file.size);
  }
  return send(`${slot}/complete`, { method: "POST" });
}
