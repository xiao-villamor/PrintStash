import { emitUnauthorized, getStoredToken } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { queryClient, invalidateQueriesForPath } from "@/lib/query-client";
import {
  getSessionVersion,
  expireSessionForFailure,
  withSessionRequest,
  type SessionRequest,
} from "@/lib/session-transport";
import type { DerivativeState } from "@/types";

const API_BASE = import.meta.env.VITE_API_URL || "";
const WS_BASE = import.meta.env.VITE_WS_URL || "";
function isBrowser(): boolean {
  // `"window" in globalThis` rather than a `typeof` probe: the question is
  // "am I running in a document?", which the global's presence answers.
  return "window" in globalThis;
}

function browserBase(): string {
  return "";
}

function serverBase(): string {
  return API_BASE || "http://localhost:8000";
}

function activeBase(): string {
  return isBrowser() ? browserBase() : serverBase();
}

export function getUrl(path: string): string {
  const base = activeBase();
  if (!base) return path;
  return `${base.replace(/\/$/, "")}${path}`;
}

export function getAssetUrl(path: string): string {
  return getUrl(path);
}

interface ResponseContext {
  /** Fence response bodies against the lifetime that requested them. */
  assertCurrent(): void;
}
interface RequestContext extends ResponseContext {
  readonly signal: AbortSignal;
}

/** Revalidate artifact bytes; failed cross-origin delivery retries with the same credentials. */
async function fetchArtifact(
  path: string,
  session: RequestContext,
  delivery: { headers: Record<string, string>; credentials?: RequestCredentials } = {
    headers: authHeaders(),
  },
): Promise<Response> {
  const options: RequestInit = {
    ...delivery,
    cache: "no-cache",
    signal: session.signal,
  };
  const proxy = async () => {
    session.assertCurrent();
    const response = await fetch(getUrl(path), {
      ...options,
      headers: { ...delivery.headers, "X-PrintStash-Delivery": "proxy" },
    });
    session.assertCurrent();
    return response;
  };
  let response: Response;
  try {
    response = await fetch(getUrl(path), options);
  } catch (error) {
    session.assertCurrent();
    if (!(error instanceof TypeError)) throw error;
    return proxy();
  }
  session.assertCurrent();
  // Reauthorize once when an expired provider capability returns an HTTP failure.
  return response.redirected && !response.ok ? proxy() : response;
}

async function withApiSession<T>(
  operation: (session: SessionRequest) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const version = getSessionVersion();
  try {
    return await withSessionRequest(operation, signal);
  } catch (error) {
    // The scoped error parser fenced the body before returning an ApiError.
    // Expire only after scope completion so this genuine 401 stays an ApiError.
    if (error instanceof ApiError && error.status === 401) {
      expireSessionForFailure(version, error);
    }
    throw error;
  }
}

export async function getAuthenticatedBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  return withApiSession(async (session) => {
    const res = await fetchArtifact(path, session);
    await expectOk(res, session);
    return res.blob();
  }, signal);
}

/** A published binary derivative, or its pending preparation state. */
export type DerivedBlob = { ready: true; blob: Blob } | { ready: false };

async function consumeDerivedBlob(
  response: Response,
  context: ResponseContext,
): Promise<DerivedBlob> {
  await expectOk(response, context);
  if (response.status === 202) return { ready: false };
  return { ready: true, blob: await response.blob() };
}

export async function getDerivedBlob(path: string, signal?: AbortSignal): Promise<DerivedBlob> {
  return withApiSession(
    async (session) => consumeDerivedBlob(await fetchArtifact(path, session), session),
    signal,
  );
}

/** Read a protected text resource while preserving the shared 401 handling. */
export async function getAuthenticatedText(path: string, signal?: AbortSignal): Promise<string> {
  return withApiSession(async (session) => {
    const res = await fetchArtifact(path, session);
    await expectOk(res, session);
    return res.text();
  }, signal);
}

/**
 * A resource served from a derivative: its text once derived, or the
 * derivative's state while it is not (the server answers 202, never a body
 * that could be mistaken for the resource).
 */
export type DerivedText = { ready: true; text: string } | { ready: false; state: DerivativeState };

const DERIVATIVE_STATES: readonly DerivativeState[] = [
  "pending",
  "queued",
  "running",
  "ready",
  "skipped",
  "failed",
  "cancelled",
];

async function consumeDerivedText(
  response: Response,
  context: ResponseContext,
): Promise<DerivedText> {
  await expectOk(response, context);
  if (response.status === 202) {
    const body: { state?: unknown } | null = await response.json();
    context.assertCurrent();
    const state = DERIVATIVE_STATES.find((known) => known === body?.state);
    if (state === undefined) throw new Error("derivative_state_invalid");
    return { ready: false, state };
  }
  return { ready: true, text: await response.text() };
}

export async function getDerivedText(path: string, signal?: AbortSignal): Promise<DerivedText> {
  return withApiSession(
    async (session) => consumeDerivedText(await fetchArtifact(path, session), session),
    signal,
  );
}

/** Public token reads follow their caller only; private scope changes never cancel them. */
async function withCallerRequest<T>(
  operation: (request: RequestContext) => Promise<T>,
  signal?: AbortSignal,
): Promise<T> {
  const caller = signal ?? new AbortController().signal;
  const context: RequestContext = {
    signal: caller,
    assertCurrent() {
      if (caller.aborted) throw caller.reason;
    },
  };
  try {
    context.assertCurrent();
    const value = await operation(context);
    context.assertCurrent();
    return value;
  } catch (error) {
    context.assertCurrent();
    throw error;
  }
}

/** Explicit public transport: no cookies, bearer credentials, private cancellation or auth events. */
export function requestPublicApi<T>(
  path: string,
  options: RequestInit = {},
  consume: (response: Response, context: RequestContext) => Promise<T> = handleResponse<T>,
): Promise<T> {
  return withCallerRequest(async (context) => {
    const headers = new Headers(options.headers);
    headers.delete("Authorization");
    const response = await fetch(getUrl(path), {
      ...options,
      headers,
      credentials: "omit",
      signal: context.signal,
    });
    context.assertCurrent();
    return consume(response, context);
  }, options.signal ?? undefined);
}

export function getPublicJson<T>(path: string, options?: { signal?: AbortSignal }): Promise<T> {
  return requestPublicApi<T>(path, { signal: options?.signal, cache: "no-store" });
}

export function getPublicDerivedBlob(path: string, signal?: AbortSignal): Promise<DerivedBlob> {
  return withCallerRequest(
    async (context) =>
      consumeDerivedBlob(
        await fetchArtifact(path, context, { headers: {}, credentials: "omit" }),
        context,
      ),
    signal,
  );
}
export function getPublicDerivedText(path: string, signal?: AbortSignal): Promise<DerivedText> {
  return withCallerRequest(
    async (context) =>
      consumeDerivedText(
        await fetchArtifact(path, context, { headers: {}, credentials: "omit" }),
        context,
      ),
    signal,
  );
}

const SAFE_DOWNLOAD_FALLBACK = "download";

/** Remove path/control characters before assigning a server-provided filename. */
export function sanitizeDownloadFilename(value: string | null | undefined): string | null {
  if (!value) return null;
  const withoutControls = Array.from(value)
    .filter((character) => {
      const code = character.charCodeAt(0);
      return code > 0x1f && code !== 0x7f;
    })
    .join("");
  const leaf = withoutControls.replaceAll("\\", "/").split("/").pop()?.trim();
  if (!leaf || leaf === "." || leaf === "..") return null;
  return leaf.replace(/[<>:"|?*]/g, "_");
}

function decodeExtendedFilename(value: string): string | null {
  const match = value.match(/^([^']*)'[^']*'(.*)$/);
  if (!match || match[1].toLowerCase() !== "utf-8") return null;
  const encoded = match[2];
  try {
    return decodeURIComponent(encoded);
  } catch {
    return null;
  }
}

/** Parse RFC 6266/RFC 5987 Content-Disposition filename parameters safely. */
export function parseContentDispositionFilename(header: string | null): string | null {
  if (!header) return null;

  const extended = header.match(/(?:^|;)\s*filename\*\s*=\s*(?:"((?:\\.|[^"])*)"|([^;]*))/i);
  const extendedValue = extended?.[1] ?? extended?.[2]?.trim();
  if (extendedValue) {
    const unescaped = extendedValue.replace(/\\([\\"])/g, "$1");
    const decoded = decodeExtendedFilename(unescaped);
    if (decoded) {
      const filename = sanitizeDownloadFilename(decoded);
      if (filename) return filename;
    }
  }

  const plain = header.match(/(?:^|;)\s*filename\s*=\s*(?:"((?:\\.|[^"])*)"|([^;]*))/i);
  const plainValue = plain?.[1] ?? plain?.[2]?.trim();
  if (!plainValue) return null;
  return sanitizeDownloadFilename(plainValue.replace(/\\([\\"])/g, "$1"));
}

/**
 * Download a protected file. Plain <a href> links can't carry the bearer
 * token, so reads gated behind auth (post-RBAC) 401. Fetch the blob with the
 * token, then trigger a save via a temporary object URL.
 */
export async function downloadAuthenticatedFile(path: string, filename?: string): Promise<void> {
  return withApiSession(async (session) => {
    const res = await fetchArtifact(path, session);
    await expectOk(res, session);
    const blob = await res.blob();
    session.assertCurrent();
    const resolvedFilename =
      sanitizeDownloadFilename(filename) ??
      parseContentDispositionFilename(res.headers.get("content-disposition")) ??
      SAFE_DOWNLOAD_FALLBACK;
    const objectUrl = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = objectUrl;
    a.download = resolvedFilename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(objectUrl);
  });
}

export function getWsUrl(path: string): string {
  if (!isBrowser()) {
    const base = (WS_BASE || API_BASE || "http://localhost:8000").replace(/\/$/, "");
    return base.replace(/^http/, "ws") + path;
  }
  if (WS_BASE) {
    return WS_BASE.replace(/\/$/, "") + path;
  }
  if (API_BASE && !API_BASE.includes("://api:")) {
    return API_BASE.replace(/\/$/, "").replace(/^http/, "ws") + path;
  }

  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}

/**
 * Decode FastAPI's error envelope. Coded errors arrive as
 * `{"detail": "model_not_found"}`; 422s put a list of field objects in
 * `detail`, and a proxy can return HTML instead of JSON. Only the string form
 * is a detail code — everything else has none.
 */
function parseDetailCode(body: string): string | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (!(parsed instanceof Object) || !("detail" in parsed)) return null;
  // Stringifying leaves a value identical to itself only when it already was a
  // string, so this accepts the string form of `detail` and nothing else — the
  // same test a `typeof` probe would make on this still-unvalidated member.
  const detail = String(parsed.detail);
  return Object.is(detail, parsed.detail) ? detail : null;
}

function errorCode(status: number, body: string): string {
  return parseDetailCode(body) ?? String(status);
}

async function parseError(res: Response, context?: ResponseContext): Promise<ApiError> {
  context?.assertCurrent();
  const text = await res.text().catch((error) => {
    context?.assertCurrent();
    if (error instanceof DOMException && error.name === "AbortError") throw error;
    return "Unknown error";
  });
  context?.assertCurrent();
  if (!context && res.status === 401) emitUnauthorized();
  return new ApiError(res.status, errorCode(res.status, text), text);
}

export async function handleResponse<T>(res: Response, context?: ResponseContext): Promise<T> {
  context?.assertCurrent();
  if (!res.ok) throw await parseError(res, context);
  if (res.status === 204) {
    // SAFETY: a 204 has no body; void endpoints declare T as void/undefined.
    return undefined as T;
  }
  const value: T = await res.json();
  context?.assertCurrent();
  return value;
}

export async function expectOk(res: Response, context?: ResponseContext): Promise<void> {
  context?.assertCurrent();
  if (!res.ok) throw await parseError(res, context);
}

/** Scope the entire response consumer, including binary bodies and mutation effects. */
export function requestApi<T>(
  path: string,
  options: RequestInit = {},
  consume: (response: Response, session: SessionRequest) => Promise<T> = handleResponse<T>,
): Promise<T> {
  return withApiSession(async (session) => {
    const response = await fetch(getUrl(path), {
      ...options,
      headers: options.headers ?? authHeaders(),
      signal: session.signal,
    });
    session.assertCurrent();
    return consume(response, session);
  }, options.signal ?? undefined);
}

export function authHeaders(): Record<string, string> {
  const token = getStoredToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function jsonHeaders(): Record<string, string> {
  const headers = authHeaders();
  headers["Content-Type"] = "application/json";
  return headers;
}

/**
 * Compatibility bridge for endpoint clients awaiting feature-owned reconciliation.
 * There is no transport cache. Remove this adapter with the final I10 cutover.
 */
export function invalidateApiCache(path?: string): void {
  if (path === undefined) void queryClient.invalidateQueries();
  else invalidateQueriesForPath(path);
}

/** Compatibility mutation adapter: validate the acknowledgement before invalidation. */
export function requestMutation<T>(
  path: string,
  options: RequestInit,
  invalidationPath = path,
): Promise<T> {
  return requestApi<T>(path, options, async (response, session) => {
    const value = await handleResponse<T>(response, session);
    session.assertCurrent();
    invalidateApiCache(invalidationPath);
    return value;
  });
}

export interface GetJsonOptions {
  signal?: AbortSignal;
  /** Compatibility only: JSON transport always reads the network. Remove in I10. */
  fresh?: boolean;
}

export function getJson<T>(path: string, options?: GetJsonOptions): Promise<T> {
  return requestApi<T>(path, { signal: options?.signal, cache: "no-store" });
}

export async function sendJson<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH",
  // The outbound side of the boundary has nothing to parse: the typed wrapper in
  // `src/lib/api` owns the endpoint's request DTO and this transport only serialises
  // it. A `JsonValue` union cannot express that either, because TypeScript never
  // accepts an `interface` — which every DTO in `@/types` is — as assignable to an
  // index signature (microsoft/TypeScript#15300).
  // oxlint-disable-next-line anti-slop/no-unknown-parameters -- outbound payload, owned and typed by the calling wrapper
  body: unknown,
  headers: Record<string, string> = {},
): Promise<T> {
  return requestApi<T>(
    path,
    {
      method,
      headers: { ...jsonHeaders(), ...headers },
      body: JSON.stringify(body),
    },
    async (response, session) => {
      const value = await handleResponse<T>(response, session);
      session.assertCurrent();
      invalidateApiCache(path);
      return value;
    },
  );
}

export function sendForm<T>(path: string, formData: FormData, signal?: AbortSignal): Promise<T> {
  return requestApi<T>(
    path,
    { method: "POST", body: formData, signal },
    async (response, session) => {
      const value = await handleResponse<T>(response, session);
      session.assertCurrent();
      invalidateApiCache(path);
      return value;
    },
  );
}

/** Multipart transfer with browser upload progress, used for large ZIP archives. */
export function sendFormWithProgress<T>(
  path: string,
  formData: FormData,
  signal: AbortSignal,
  onProgress: (loaded: number, total: number) => void,
): Promise<T> {
  return withApiSession(async (session) => {
    const response = await new Promise<Response>((resolve, reject) => {
      const request = new XMLHttpRequest();
      let settled = false;
      const finish = () => {
        settled = true;
        session.signal.removeEventListener("abort", abort);
      };
      const abort = () => request.abort();
      request.open("POST", getUrl(path));
      for (const [name, value] of Object.entries(authHeaders()))
        request.setRequestHeader(name, value);
      request.upload.onprogress = (event) => {
        if (session.signal.aborted || settled) return;
        if (event.lengthComputable && event.total > 0) onProgress(event.loaded, event.total);
      };
      request.onload = () => {
        if (settled) return;
        finish();
        resolve(new Response(request.responseText, { status: request.status }));
      };
      request.onerror = () => {
        if (settled) return;
        finish();
        reject(new TypeError("Failed to fetch"));
      };
      request.onabort = () => {
        if (settled) return;
        finish();
        reject(session.signal.reason);
      };
      session.signal.addEventListener("abort", abort, { once: true });
      request.send(formData);
    });
    const value = await handleResponse<T>(response, session);
    session.assertCurrent();
    invalidateApiCache(path);
    return value;
  }, signal);
}

export function sendAction(path: string, method: "POST" | "DELETE"): Promise<void> {
  return requestApi(path, { method }, async (response, session) => {
    await expectOk(response, session);
    session.assertCurrent();
    invalidateApiCache(path);
  });
}
