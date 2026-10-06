import { onAuthChange } from "@/lib/auth-store";

/** A session incarnation, including renewed logins by the same account. */
let sessionVersion = 0;
let sessionController = new AbortController();

onAuthChange(() => {
  const previous = sessionController;
  sessionVersion += 1;
  sessionController = new AbortController();
  previous.abort(new DOMException("request_session_changed", "AbortError"));
});

export function getSessionVersion(): number {
  return sessionVersion;
}

export function requireSessionVersion(version: number): void {
  if (version !== sessionVersion) throw new DOMException("request_session_changed", "AbortError");
}

export interface SessionRequest {
  readonly signal: AbortSignal;
  /** Fence headers, parsed bodies, progress and side effects before publishing. */
  assertCurrent(): void;
}

/** Keep cancellation active through body consumption, not merely response headers. */
export async function withSessionRequest<T>(
  operation: (request: SessionRequest) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const version = sessionVersion;
  const combined = signal
    ? AbortSignal.any([sessionController.signal, signal])
    : sessionController.signal;
  const request: SessionRequest = {
    signal: combined,
    assertCurrent() {
      requireSessionVersion(version);
      combined.throwIfAborted();
    },
  };
  request.assertCurrent();
  try {
    const value = await operation(request);
    request.assertCurrent();
    return value;
  } catch (error) {
    request.assertCurrent();
    throw error;
  }
}
