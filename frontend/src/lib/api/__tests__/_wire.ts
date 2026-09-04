/**
 * Shared fetch stand-in for the API client mirrors.
 *
 * Every module under `src/lib/api` is a thin translation from a function call to one
 * HTTP request, so what its tests defend is the **wire contract**: the path, the verb,
 * the body, and whether the response is allowed to come from cache. A drift in any of
 * those is silent — the UI keeps compiling and simply stops working against the backend
 * — which is why these tests assert on the request that was made rather than on a
 * returned object alone.
 */
import { expect, vi } from "vitest";

/** One value inside a JSON body the fake backend hands back. */
export type WireValue =
  | string
  | number
  | boolean
  | null
  | readonly WireValue[]
  | { readonly [key: string]: WireValue };

/** The request one API client made, as the assertions here need to see it. */
export interface RecordedRequest {
  readonly url: string;
  readonly init: RequestInit;
}

export const fetchMock = vi.fn<typeof fetch>();

function jsonResponse(data: WireValue, status = 200): Response {
  // A 204 carries no body at all, and `new Response` rejects one outright.
  const body = status === 204 ? null : JSON.stringify(data);
  return new Response(body, { status, headers: { "content-type": "application/json" } });
}

/**
 * Answer every fetch with a freshly built response.
 *
 * A `Response` body can only be read once, so a single shared instance would break any
 * test that calls the client twice.
 */
export function respondWith(data: WireValue, status = 200): void {
  fetchMock.mockImplementation(() => Promise.resolve(jsonResponse(data, status)));
}

/** The request the client actually made, for the assertions that matter. */
export function lastCall(): RecordedRequest {
  const [url, init] = fetchMock.mock.calls.at(-1)!;
  return { url: String(url), init: init ?? {} };
}

/** The JSON body of the last request, parsed back into wire values. */
export function lastBody(): WireValue {
  const { init } = lastCall();
  // SAFETY: every caller of this helper sends a JSON body built in the test
  // above it, so the round trip through `JSON.parse` yields a `WireValue`.
  return JSON.parse(String(init.body)) as WireValue;
}

/** The multipart body of the last request. */
export function lastForm(): FormData {
  const { init } = lastCall();
  if (!(init.body instanceof FormData)) {
    throw new Error("the last request did not send a multipart body");
  }
  return init.body;
}

/** Assert the last request's path and verb in one line. */
export function expectRequest(url: string, method = "GET"): void {
  const call = lastCall();
  expect(call.url).toBe(url);
  expect((call.init.method ?? "GET").toUpperCase()).toBe(method);
}
