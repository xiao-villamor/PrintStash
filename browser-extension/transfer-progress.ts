import { httpPanelNetwork, vaultFetch } from "./vault-fetch.ts";

export type ByteProgress = (loaded: number, total: number | null) => void;

/** Encoded Content-Length describes wire bytes, not the decoded stream we consume. */
export function downloadProgress(response: Response, progress?: ByteProgress) {
  const length = response.headers.get("Content-Length");
  const value = length === null ? null : Number(length);
  const total =
    !response.headers.get("Content-Encoding") &&
    value !== null &&
    Number.isSafeInteger(value) &&
    value > 0
      ? value
      : null;
  return (loaded: number) => progress?.(loaded, total !== null && loaded <= total ? total : null);
}

export function progressLabel(loaded: number, total: number | null): string {
  const bytes = `${(loaded / (1024 * 1024)).toFixed(1)} MiB`;
  return total !== null && total > 0
    ? `${bytes} · ${Math.min(100, Math.floor((loaded / total) * 100))}%`
    : `${bytes} · size unknown`;
}

/** XHR exposes upload byte events; fetch remains the owner of ordinary requests. */
export function captureTransferFetch(
  progress: ByteProgress,
  fetchImpl: typeof fetch = vaultFetch,
): typeof fetch {
  return async (input, init) => {
    if (init?.method !== "PUT" || !(init.body instanceof Blob)) return fetchImpl(input, init);
    const workerNetwork = httpPanelNetwork(input);
    if (workerNetwork) return workerNetwork(input, init, progress);
    const body = init.body;
    const signal = init.signal;
    signal?.throwIfAborted();
    return new Promise<Response>((resolve, reject) => {
      const request = new XMLHttpRequest();
      const cleanup = () => signal?.removeEventListener("abort", abort);
      const fail = (error: Error) => {
        cleanup();
        reject(error);
      };
      const abort = () => {
        request.abort();
        fail(new DOMException("The operation was aborted.", "AbortError"));
      };
      request.open("PUT", String(input));
      request.timeout = 120_000;
      // PrintStash authorization is the explicit Bearer header. Cookies for the
      // server's host still go along, so a sign-in proxy in front of it passes.
      request.withCredentials = true;
      for (const [name, value] of new Headers(init.headers)) request.setRequestHeader(name, value);
      request.upload.onprogress = (event) => {
        if (!signal?.aborted) progress(event.loaded, event.lengthComputable ? event.total : null);
      };
      request.onload = () => {
        cleanup();
        resolve(
          new Response(request.status === 204 ? null : request.responseText, {
            status: request.status,
            headers: {
              "Content-Type": request.getResponseHeader("Content-Type") ?? "application/json",
            },
          }),
        );
      };
      request.onerror = () =>
        fail(
          new Error("Transfer connection failed. Reopen this source to recover confirmed files."),
        );
      request.ontimeout = () =>
        fail(new Error("Transfer timed out. Reopen this source to recover confirmed files."));
      request.onabort = () => fail(new DOMException("The operation was aborted.", "AbortError"));
      signal?.addEventListener("abort", abort, { once: true });
      try {
        signal?.throwIfAborted();
        request.send(body);
      } catch (error) {
        cleanup();
        reject(error);
      }
    });
  };
}
