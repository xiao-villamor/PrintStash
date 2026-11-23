import { captureTransferFetch } from "./transfer-progress.ts";
import { vaultFetch } from "./vault-fetch.ts";
import type { VaultWorkerReply, VaultWorkerRequest } from "./vault-worker-transport.ts";

globalThis.addEventListener("message", (event: MessageEvent<VaultWorkerRequest>) => {
  const [port] = event.ports;
  const { url, init, body, reportProgress } = event.data;
  const controller = new AbortController();
  port.onmessage = () => controller.abort();

  const reply = (data: VaultWorkerReply) =>
    port.postMessage(data, data.type === "response" && data.body ? [data.body] : []);
  const send = reportProgress
    ? captureTransferFetch((loaded, total) => reply({ type: "progress", loaded, total }))
    : vaultFetch;

  void (async () => {
    try {
      let requestBody: BodyInit | null;
      if (Array.isArray(body)) {
        const form = new FormData();
        for (const field of body) {
          const [name, value, filename] = field;
          if (typeof value === "string") form.append(name, value);
          else form.append(name, value, filename);
        }
        requestBody = form;
      } else {
        requestBody = body;
      }
      const response = await send(url, { ...init, body: requestBody, signal: controller.signal });
      const headers: [string, string][] = [];
      response.headers.forEach((value, name) => headers.push([name, value]));
      reply({
        type: "response",
        body: response.body,
        status: response.status,
        statusText: response.statusText,
        headers,
        url: response.url,
        redirected: response.redirected,
        responseType: response.type,
      });
    } catch (error) {
      reply({ type: "error", message: error instanceof Error ? error.message : String(error) });
    } finally {
      port.close();
    }
  })();
});
