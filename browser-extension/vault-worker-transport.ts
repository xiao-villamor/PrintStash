import type { ByteProgress } from "./transfer-progress.ts";

type FormField = [name: string, value: string] | [name: string, value: Blob, filename: string];

export type VaultWorkerRequest = {
  url: string;
  init: Omit<RequestInit, "body" | "signal">;
  body: Exclude<BodyInit, FormData | ReadableStream> | FormField[] | null;
  reportProgress: boolean;
};

export type VaultWorkerReply =
  | { type: "progress"; loaded: number; total: number | null }
  | { type: "error"; message: string }
  | {
      type: "response";
      body: ReadableStream<Uint8Array> | null;
      status: number;
      statusText: string;
      headers: [string, string][];
      url: string;
      redirected: boolean;
      responseType: ResponseType;
    };

export type VaultNetwork = (
  input: Parameters<typeof fetch>[0],
  init?: RequestInit,
  progress?: ByteProgress,
) => Promise<Response>;

type VaultWorker = Pick<Worker, "addEventListener" | "removeEventListener"> & {
  postMessage(request: VaultWorkerRequest, ports: MessagePort[]): void;
};

export function workerVaultNetwork(createWorker: () => VaultWorker): VaultNetwork {
  let worker: VaultWorker | undefined;
  return async (input, init = {}, progress) => {
    if (input instanceof Request) throw new TypeError("Vault requests require a URL.");
    const { body, signal, headers, ...options } = init;
    signal?.throwIfAborted();
    if (body instanceof ReadableStream)
      throw new TypeError("Vault request streams are unsupported.");
    const requestHeaders: [string, string][] = [];
    new Headers(headers).forEach((value, name) => requestHeaders.push([name, value]));
    let requestBody: VaultWorkerRequest["body"];
    if (body instanceof FormData) {
      const fields: FormField[] = [];
      body.forEach((value, name) => {
        fields.push(typeof value === "string" ? [name, value] : [name, value, value.name]);
      });
      requestBody = fields;
    } else {
      requestBody = body ?? null;
    }

    const activeWorker = (worker ??= createWorker());
    const { port1, port2 } = new MessageChannel();
    return new Promise((resolve, reject) => {
      const cleanup = () => {
        port1.close();
        signal?.removeEventListener("abort", abort);
        activeWorker.removeEventListener("error", workerError);
      };
      const abort = () => {
        port1.postMessage("abort");
        cleanup();
        reject(signal?.reason);
      };
      const workerError = () => {
        worker = undefined;
        cleanup();
        reject(new Error("The PrintStash network worker failed."));
      };
      port1.onmessage = ({ data }: MessageEvent<VaultWorkerReply>) => {
        if (data.type === "progress") {
          progress?.(data.loaded, data.total);
          return;
        }
        cleanup();
        if (data.type === "error") {
          reject(new Error(data.message));
          return;
        }
        try {
          const body =
            data.body && signal
              ? data.body.pipeThrough(new TransformStream(), { signal })
              : data.body;
          const response = data.status === 0 ? Response.error() : new Response(body, data);
          Object.defineProperties(response, {
            url: { value: data.url },
            redirected: { value: data.redirected },
            type: { value: data.responseType },
          });
          resolve(response);
        } catch (error) {
          reject(error);
        }
      };
      signal?.addEventListener("abort", abort, { once: true });
      activeWorker.addEventListener("error", workerError);
      try {
        const request: VaultWorkerRequest = {
          url: String(input),
          init: { ...options, headers: requestHeaders },
          body: requestBody,
          reportProgress: Boolean(progress),
        };
        activeWorker.postMessage(request, [port2]);
      } catch (error) {
        port2.close();
        cleanup();
        reject(error);
      }
    });
  };
}
