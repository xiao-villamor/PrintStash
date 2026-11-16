import { downloadProgress, type ByteProgress } from "./transfer-progress.ts";
import type { BrowserCaptureMessage } from "./capture-adapter.ts";
import type { BrowserCaptureFile } from "./capture-transport.ts";

export class MarketplaceDownloadError extends Error {}

/** Downloads only from adapter-approved hosts. Signed URLs and bytes stay in memory. */
export async function downloadMarketplaceFile(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  url: string;
  provider: string;
  allowedHosts: readonly string[];
  ensureOriginPermission(origin: string): Promise<void>;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
  progress?: ByteProgress;
}): Promise<BrowserCaptureFile> {
  const { candidate, signal, provider } = options;
  const url = new URL(options.url);
  if (
    url.protocol !== "https:" ||
    !options.allowedHosts.includes(url.hostname) ||
    url.port ||
    url.username ||
    url.password ||
    url.hash
  )
    throw new MarketplaceDownloadError(`${provider} returned an unsupported download host.`);
  signal?.throwIfAborted();
  await options.ensureOriginPermission(`${url.origin}/*`);
  signal?.throwIfAborted();
  const response = await (options.fetchImpl ?? fetch)(url.href, {
    credentials: "omit",
    redirect: "error",
    signal,
  });
  return readMarketplaceFile(response, candidate, provider, signal, options.progress);
}

/** Common bounded reader; each adapter owns the request's authentication and URL policy. */
export async function readMarketplaceFile(
  response: Response,
  candidate: BrowserCaptureMessage["candidates"][number],
  provider: string,
  signal?: AbortSignal,
  progress?: ByteProgress,
): Promise<BrowserCaptureFile> {
  if (
    !response.ok ||
    !response.body ||
    /text\/html|application\/json/i.test(response.headers.get("Content-Type") ?? "")
  )
    throw new MarketplaceDownloadError(
      `${provider} file download failed. Refresh the selection to obtain a new download link.`,
    );
  const maximum = 512 * 1024 * 1024;
  const length = response.headers.get("Content-Length");
  if (
    length !== null &&
    (!Number.isSafeInteger(Number(length)) || Number(length) < 0 || Number(length) > maximum)
  ) {
    await response.body.cancel();
    throw new MarketplaceDownloadError(`${provider} package exceeds the browser transfer limit.`);
  }
  const report = downloadProgress(response, progress);
  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let size = 0;
  const cancel = () => {
    void reader.cancel();
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    while (true) {
      signal?.throwIfAborted();
      const next = await reader.read();
      if (next.done) break;
      size += next.value.byteLength;
      if (size > maximum) {
        await reader.cancel();
        throw new MarketplaceDownloadError(
          `${provider} package exceeds the browser transfer limit.`,
        );
      }
      const chunk = new ArrayBuffer(next.value.byteLength);
      new Uint8Array(chunk).set(next.value);
      chunks.push(chunk);
      report(size);
    }
    signal?.throwIfAborted();
  } finally {
    signal?.removeEventListener("abort", cancel);
    reader.releaseLock();
  }
  if (size === 0) throw new MarketplaceDownloadError(`${provider} returned an empty file.`);
  if (length !== null && !response.headers.get("Content-Encoding") && size !== Number(length))
    throw new MarketplaceDownloadError(
      `${provider} file download was incomplete. Retry this file.`,
    );
  return {
    id: candidate.id,
    filename: candidate.filename,
    file: new Blob(chunks),
    mediaType: "application/octet-stream",
  };
}
