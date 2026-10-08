/** Transfer-only boundary between the STL worker and the mesh viewer. */
export type StlParseReply =
  | { state: "parsed"; positions: Float32Array<ArrayBuffer>; normals: Float32Array<ArrayBuffer> }
  | { state: "failed"; reason: string };

export interface StlParserWorker {
  onmessage: ((event: MessageEvent<StlParseReply>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  postMessage(buffer: ArrayBuffer, transfer: Transferable[]): void;
  terminate(): void;
}

export type StlWorkerFactory = () => StlParserWorker;

export async function parseStl(
  url: string,
  signal: AbortSignal,
  createWorker: StlWorkerFactory = () =>
    new Worker(new URL("../workers/stl-parser.worker.ts", import.meta.url), { type: "module" }),
): Promise<Extract<StlParseReply, { state: "parsed" }>> {
  signal.throwIfAborted();
  const response = await fetch(url, { signal });
  if (!response.ok) throw new Error(`stl_download_failed:${response.status}`);
  const buffer = await response.arrayBuffer();
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const worker = createWorker();
    const cleanup = () => {
      signal.removeEventListener("abort", abort);
      worker.onmessage = null;
      worker.onerror = null;
      worker.terminate();
    };
    const abort = () => {
      cleanup();
      reject(new DOMException("STL parsing cancelled", "AbortError"));
    };
    worker.onmessage = ({ data }) => {
      cleanup();
      if (data.state === "parsed") resolve(data);
      else reject(new Error(data.reason));
    };
    worker.onerror = () => {
      cleanup();
      reject(new Error("stl_worker_failed"));
    };
    signal.addEventListener("abort", abort, { once: true });
    if (signal.aborted) {
      abort();
      return;
    }
    try {
      worker.postMessage(buffer, [buffer]);
    } catch (error) {
      cleanup();
      reject(error);
    }
  });
}
