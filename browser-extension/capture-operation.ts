import { normalizeVault } from "./core.ts";

export class CaptureRetiredError extends Error {
  constructor() {
    super("Capture was cancelled because the connection changed.");
    this.name = "CaptureRetiredError";
  }
}

/** Abort even a boundary that ignores its signal; its late result cannot publish. */
export async function runCaptureRequest<T>(
  signals: readonly (AbortSignal | undefined)[],
  operation: (signal: AbortSignal) => Promise<T>,
): Promise<T> {
  const controller = new AbortController();
  const abort = () => controller.abort();
  for (const signal of signals) {
    if (signal?.aborted) controller.abort();
    signal?.addEventListener("abort", abort, { once: true });
  }
  try {
    if (controller.signal.aborted)
      throw new DOMException("The operation was aborted.", "AbortError");
    return await new Promise<T>((resolve, reject) => {
      const retire = () => reject(new DOMException("The operation was aborted.", "AbortError"));
      controller.signal.addEventListener("abort", retire, { once: true });
      Promise.resolve()
        .then(() => {
          if (controller.signal.aborted)
            throw new DOMException("The operation was aborted.", "AbortError");
          return operation(controller.signal);
        })
        .then(
          (result) => {
            controller.signal.removeEventListener("abort", retire);
            if (controller.signal.aborted)
              reject(new DOMException("The operation was aborted.", "AbortError"));
            else resolve(result);
          },
          (error) => {
            controller.signal.removeEventListener("abort", retire);
            reject(error);
          },
        );
    });
  } finally {
    for (const signal of signals) signal?.removeEventListener("abort", abort);
  }
}

export interface CaptureOperation<Config extends { vault: string }> {
  readonly config: Readonly<Config>;
  readonly authorization: string;
  readonly signal: AbortSignal;
  assertCurrent(): void;
  run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T>;
}

/** A single capture owns one immutable connection until completion or retirement. */
export class CaptureOperationOwner {
  private current: AbortController | null = null;

  begin<Config extends { vault: string }>(
    config: Config,
    authorization: string,
  ): CaptureOperation<Config> {
    if (this.current) throw new Error("A capture is already running.");
    if (!authorization)
      throw new Error("The browser connection expired. Connect PrintStash again.");
    const controller = new AbortController();
    const connection = Object.freeze({ ...config, vault: normalizeVault(config.vault) });
    this.current = controller;
    const assertCurrent = () => {
      if (this.current !== controller || controller.signal.aborted) throw new CaptureRetiredError();
    };
    return {
      config: connection,
      authorization,
      signal: controller.signal,
      assertCurrent,
      async run<T>(operation: (signal: AbortSignal) => Promise<T>): Promise<T> {
        assertCurrent();
        const result = await runCaptureRequest([controller.signal], operation);
        assertCurrent();
        return result;
      },
    };
  }

  isCurrent(operation: CaptureOperation<{ vault: string }>): boolean {
    return this.current?.signal === operation.signal && !operation.signal.aborted;
  }

  finish(operation: CaptureOperation<{ vault: string }>): boolean {
    if (!this.isCurrent(operation)) return false;
    const current = this.current;
    this.current = null;
    current?.abort();
    return true;
  }

  retire(): void {
    const current = this.current;
    this.current = null;
    current?.abort();
  }
}
