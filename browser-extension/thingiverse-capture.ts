import { buildBrowserCaptureMessage, type BrowserCaptureMessage } from "./capture-adapter.ts";
import type { BrowserCaptureFile } from "./capture-transport.ts";

export const THINGIVERSE_MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024;

const THINGIVERSE_DOWNLOAD_HOSTS = new Set([
  "thingiverse.com",
  "www.thingiverse.com",
  "api.thingiverse.com",
  "cdn.thingiverse.com",
]);

function archiveId(sourceItemId: string): string {
  return `thingiverse:${sourceItemId}:archive`;
}

function archiveFilename(sourceItemId: string): string {
  return `thingiverse-${sourceItemId}.zip`;
}

function isSafeThingiverseUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "https:" &&
      !parsed.username &&
      !parsed.password &&
      !parsed.hash &&
      THINGIVERSE_DOWNLOAD_HOSTS.has(parsed.hostname.toLowerCase())
    );
  } catch {
    return false;
  }
}

export function thingiverseCaptureFromPage({
  pageUrl,
  pageTitle,
  jsonLd,
}: {
  pageUrl: string;
  pageTitle?: string;
  jsonLd: string[];
}): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "Thingiverse",
    pageUrl,
    pageTitle,
    jsonLd,
  });
  const sourceItemId = capture.source.source_item_id;
  if (!sourceItemId) return capture;
  return {
    schema_version: capture.schema_version,
    kind: capture.kind,
    source: capture.source,
    state: "ready",
    candidates: [
      {
        id: archiveId(sourceItemId),
        filename: archiveFilename(sourceItemId),
        fileType: "other",
        mediaType: "application/zip",
      },
    ],
  };
}

async function readBoundedArchive(response: Response, signal?: AbortSignal): Promise<Blob> {
  const contentLengthValue = response.headers.get("Content-Length");
  if (contentLengthValue !== null) {
    const contentLength = Number(contentLengthValue);
    if (
      !Number.isSafeInteger(contentLength) ||
      contentLength < 0 ||
      contentLength > THINGIVERSE_MAX_FILE_SIZE_BYTES
    ) {
      throw new Error(
        "user_file_required: The Thingiverse archive is too large. Download it normally, then attach it in Pending Imports.",
      );
    }
  }
  if (!response.body) {
    throw new Error(
      "user_file_required: Thingiverse did not provide an archive stream. Download it normally, then attach it in Pending Imports.",
    );
  }
  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let total = 0;
  const abortReader = () => void reader.cancel();
  signal?.addEventListener("abort", abortReader, { once: true });
  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel();
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > THINGIVERSE_MAX_FILE_SIZE_BYTES) {
        await reader.cancel();
        throw new Error(
          "user_file_required: The Thingiverse archive is too large. Download it normally, then attach it in Pending Imports.",
        );
      }
      const copy = new ArrayBuffer(next.value.byteLength);
      new Uint8Array(copy).set(next.value);
      chunks.push(copy);
    }
  } finally {
    signal?.removeEventListener("abort", abortReader);
    reader.releaseLock();
  }
  return new Blob(chunks, { type: "application/zip" });
}

export async function downloadThingiverseArchive({
  fetchImpl = fetch,
  sourceItemId,
  ensureOriginPermission = async () => {},
  signal,
}: {
  fetchImpl?: typeof fetch;
  sourceItemId: string;
  ensureOriginPermission?: (origin: string) => Promise<void>;
  signal?: AbortSignal;
}): Promise<BrowserCaptureFile> {
  if (!/^\d{1,20}$/.test(sourceItemId)) {
    throw new Error(
      "user_file_required: Thingiverse model identity changed. Download the archive normally, then attach it in Pending Imports.",
    );
  }
  const sourceUrl = `https://www.thingiverse.com/thing:${sourceItemId}/zip`;
  await ensureOriginPermission("https://www.thingiverse.com/*");
  const response = await fetchImpl(sourceUrl, {
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (response.status === 401 || response.status === 403 || response.status === 429) {
    throw new Error(
      "user_file_required: Thingiverse requires a browser check. Complete it in this tab, then try again, or attach the archive in Pending Imports.",
    );
  }
  if (!response.ok) {
    throw new Error(
      "user_file_required: Thingiverse could not provide the model archive. Download it normally, then attach it in Pending Imports.",
    );
  }
  if (response.url && !isSafeThingiverseUrl(response.url)) {
    throw new Error(
      "user_file_required: Thingiverse redirected the archive to an unsafe host. Attach it manually in Pending Imports.",
    );
  }
  const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
  if (contentType.includes("text/html")) {
    throw new Error(
      "user_file_required: Thingiverse requires a browser check. Complete it in this tab, then try again, or attach the archive in Pending Imports.",
    );
  }
  return {
    id: archiveId(sourceItemId),
    file: await readBoundedArchive(response, signal),
    filename: archiveFilename(sourceItemId),
    mediaType: "application/zip",
  };
}
