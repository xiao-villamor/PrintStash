import { emitUnauthorized, onAuthChange } from "@/lib/auth-store";

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

// A current request's verified failure can itself retire the session. Preserve
// that exact failure through enclosing workflow scopes instead of calling it
// cancellation. Weak references avoid keeping completed failures alive.
const retirementFailures = new WeakSet<Error>();

export function expireSessionForFailure(version: number, failure: Error): void {
  requireSessionVersion(version);
  retirementFailures.add(failure);
  emitUnauthorized();
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
  const controller = new AbortController();
  const sources = signal ? [sessionController.signal, signal] : [sessionController.signal];
  const bindings = sources.map((source) => ({
    source,
    cancel: () => controller.abort(source.reason),
  }));
  const release = () => {
    for (const { source, cancel } of bindings) source.removeEventListener("abort", cancel);
    controller.signal.removeEventListener("abort", release);
  };
  controller.signal.addEventListener("abort", release, { once: true });
  for (const { source, cancel } of bindings) {
    if (source.aborted) {
      cancel();
      break;
    }
    source.addEventListener("abort", cancel, { once: true });
  }
  const combined = controller.signal;
  const request: SessionRequest = {
    signal: combined,
    assertCurrent() {
      if (combined.aborted) throw combined.reason;
      requireSessionVersion(version);
    },
  };
  try {
    request.assertCurrent();
    const value = await operation(request);
    request.assertCurrent();
    return value;
  } catch (error) {
    if (error instanceof Error && retirementFailures.has(error)) throw error;
    request.assertCurrent();
    throw error;
  } finally {
    release();
  }
}
