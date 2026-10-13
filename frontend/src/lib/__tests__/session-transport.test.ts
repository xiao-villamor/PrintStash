/**
 * Session scopes combine caller and account cancellation without newer browser APIs.
 * A completed scope releases subscriptions so later cancellation cannot affect it.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { withSessionRequest } from "@/lib/session-transport";

afterEach(() => vi.restoreAllMocks());

describe("withSessionRequest", () => {
  it("works without AbortSignal.any", async () => {
    vi.spyOn(AbortSignal, "any").mockImplementation(() => {
      throw new Error("Unsupported browser API");
    });
    const caller = new AbortController();
    await expect(withSessionRequest(async () => "body", caller.signal)).resolves.toBe("body");
  });

  it.each([
    { label: "caller", cancel: (caller: AbortController) => caller.abort() },
    { label: "session", cancel: () => clearLogin() },
  ])("releases $label cancellation after completion", async ({ cancel }) => {
    const caller = new AbortController();
    const signal = await withSessionRequest(async (request) => request.signal, caller.signal);
    cancel(caller);
    expect(signal.aborted).toBe(false);
  });

  it("preserves an already cancelled caller's reason", async () => {
    const caller = new AbortController();
    const reason = new DOMException("Navigation", "AbortError");
    caller.abort(reason);
    const operation = vi.fn<() => Promise<string>>().mockResolvedValue("body");
    await expect(withSessionRequest(operation, caller.signal)).rejects.toBe(reason);
    expect(operation).not.toHaveBeenCalled();
  });

  it("preserves the first cancellation reason", async () => {
    const caller = new AbortController();
    const body = Promise.withResolvers<string>();
    const reason = new DOMException("Navigation", "AbortError");
    const pending = withSessionRequest(() => body.promise, caller.signal);
    const outcome = pending.catch((error: Error) => error);
    caller.abort(reason);
    clearLogin();
    body.resolve("body");
    expect(await outcome).toBe(reason);
  });
});
