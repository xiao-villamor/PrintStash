/** Canceling a preview terminates its parser, including an in-progress long arc. */
import type { ToolpathData } from "./gcode";

export type ToolpathWorkerReply =
  | { kind: "ready"; data: ToolpathData }
  | { kind: "error"; code: "limit" | "invalid" };
export class ToolpathParseError extends Error {
  constructor(public readonly code: "limit" | "invalid") {
    super(`toolpath_${code}`);
  }
}
export interface ToolpathWorker {
  onmessage: ((event: MessageEvent<ToolpathWorkerReply>) => void) | null;
  onerror: ((event: ErrorEvent) => void) | null;
  postMessage(message: { text: string }): void;
  terminate(): void;
}
export function createToolpathWorker(): ToolpathWorker {
  return new Worker(new URL("./gcode-worker.ts", import.meta.url), { type: "module" });
}

export function parseGcodeInWorker(
  text: string,
  signal: AbortSignal,
  createWorker = createToolpathWorker,
): Promise<ToolpathData> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("Preview cancelled", "AbortError"));
      return;
    }
    const worker = createWorker();
    const clean = () => {
      worker.terminate();
      signal.removeEventListener("abort", abort);
    };
    const abort = () => {
      clean();
      reject(new DOMException("Preview cancelled", "AbortError"));
    };
    worker.onmessage = (event: MessageEvent<ToolpathWorkerReply>) => {
      clean();
      if (event.data.kind === "ready") resolve(event.data.data);
      else reject(new ToolpathParseError(event.data.code));
    };
    worker.onerror = () => {
      clean();
      reject(new Error("toolpath_worker_failed"));
    };
    signal.addEventListener("abort", abort, { once: true });
    try {
      worker.postMessage({ text });
    } catch (error) {
      clean();
      reject(error);
    }
  });
}
