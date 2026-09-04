export interface CompletionChainedPollerOptions<T> {
  request: (signal: AbortSignal) => Promise<T>;
  intervalMs: number;
  shouldContinue: (result: T, forceContinue: boolean) => boolean;
  onResult: (result: T) => void;
  onError?: (error: Error) => void;
}

export interface CompletionChainedPoller {
  /** Start a delayed polling chain if one is not already running. */
  start(forceContinue?: boolean): void;
  /** Start one request immediately, preserving the one-request invariant. */
  refresh(): void;
  /** Cancel the chain, its timer, and its current request. */
  stop(): void;
}

/**
 * Poll by waiting for each request to settle before scheduling the next one.
 * The request sequence is generation-scoped so a response that ignores
 * AbortSignal (as a test double or an older transport may) cannot update a
 * newer page state after the chain has been stopped or restarted.
 */
export function createCompletionChainedPoller<T>(
  options: CompletionChainedPollerOptions<T>,
): CompletionChainedPoller {
  let running = false;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let inFlight: { controller: AbortController; generation: number } | null = null;
  let generation = 0;
  let refreshAfterFlight = false;
  let forceContinue = false;

  function clearScheduled(): void {
    if (timer === null) return;
    clearTimeout(timer);
    timer = null;
  }

  function schedule(): void {
    if (!running || timer !== null || inFlight !== null) return;
    timer = setTimeout(() => {
      timer = null;
      void run();
    }, options.intervalMs);
  }

  async function run(): Promise<void> {
    if (!running || inFlight !== null) return;
    const controller = new AbortController();
    const requestGeneration = generation;
    const request = { controller, generation: requestGeneration };
    inFlight = request;
    let continueChain = false;
    try {
      const result = await options.request(controller.signal);
      if (
        !running ||
        controller.signal.aborted ||
        requestGeneration !== generation ||
        inFlight !== request
      ) {
        return;
      }
      options.onResult(result);
      if (
        !running ||
        controller.signal.aborted ||
        requestGeneration !== generation ||
        inFlight !== request
      ) {
        return;
      }
      continueChain = options.shouldContinue(result, forceContinue);
      if (!continueChain) stop();
    } catch (error) {
      if (
        !running ||
        controller.signal.aborted ||
        requestGeneration !== generation ||
        inFlight !== request
      ) {
        return;
      }
      options.onError?.(error instanceof Error ? error : new Error("Polling request failed."));
      continueChain = true;
    } finally {
      if (inFlight === request) {
        inFlight = null;
        if (running) {
          if (refreshAfterFlight) {
            refreshAfterFlight = false;
            void run();
          } else if (continueChain || requestGeneration !== generation) {
            schedule();
          }
        }
      }
    }
  }

  function start(force = false): void {
    if (force) forceContinue = true;
    if (running) return;
    running = true;
    schedule();
  }

  function refresh(): void {
    running = true;
    clearScheduled();
    if (inFlight !== null) {
      // Abort is advisory: some transports resolve after abort. Keep the old
      // request registered until it settles so refresh can never overlap it.
      refreshAfterFlight = true;
    } else {
      void run();
    }
  }

  function stop(): void {
    running = false;
    generation += 1;
    refreshAfterFlight = false;
    forceContinue = false;
    clearScheduled();
    const request = inFlight;
    request?.controller.abort();
  }

  return { start, refresh, stop };
}
