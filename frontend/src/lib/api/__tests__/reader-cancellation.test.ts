/** Cross-feature readers release active requests when their Query observer retires. */
import { afterEach, describe, expect, it, vi } from "vitest";
import { getModel, getModelPrintJobs, listModels } from "@/lib/api/models";
import { listMultipartModels } from "@/lib/api/multipart-models";
import { listExternalLibraries } from "@/lib/api/libraries";

afterEach(() => vi.unstubAllGlobals());
describe("shared reader cancellation", () => {
  it.each([
    { name: "Model detail", read: (signal: AbortSignal) => getModel(1, { signal }) },
    { name: "Model print jobs", read: (signal: AbortSignal) => getModelPrintJobs(1, { signal }) },
    { name: "Model choices", read: (signal: AbortSignal) => listModels({ limit: 25 }, { signal }) },
    {
      name: "Multipart destinations",
      read: (signal: AbortSignal) => listMultipartModels({ limit: 30 }, { signal }),
    },
    {
      name: "external libraries",
      read: (signal: AbortSignal) => listExternalLibraries({ signal }),
    },
  ])("aborts an active $name read", async ({ read }) => {
    const controller = new AbortController();
    let delivered: AbortSignal | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn<typeof fetch>(
        (_url, options) =>
          new Promise((_resolve, reject) => {
            const signal = options?.signal;
            if (!signal) throw new Error("Cancellation signal is required");
            delivered = signal;
            signal.addEventListener("abort", () => reject(signal.reason), { once: true });
          }),
      ),
    );
    const pending = read(controller.signal);
    const outcome = pending.catch((error: Error) => error);
    controller.abort();
    await expect(outcome).resolves.toMatchObject({ name: "AbortError" });
    expect(delivered).toMatchObject({ aborted: true });
  });
});
