import { emitUnauthorized, getStoredToken } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { queryClient, invalidateQueriesForPath } from "@/lib/query-client";

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

export async function getAuthenticatedBlob(path: string): Promise<Blob> {
  // `no-cache` (revalidate, don't blindly reuse) instead of `force-cache`:
  // thumbnail URLs are stable (e.g. /files/1/thumbnail) but their content
  // changes when a file id is reused (re-upload / DB reset). force-cache served
  // the stale image forever; the backend sends an ETag, so revalidation here is
  // a cheap 304 when unchanged and a fresh fetch when it actually changed.
  const res = await fetch(getUrl(path), {
    headers: authHeaders(),
    cache: "no-cache",
  });
  if (!res.ok) throw await parseError(res);
  return res.blob();
}

/** Read a protected text resource while preserving the shared 401 handling. */
export async function getAuthenticatedText(path: string, signal?: AbortSignal): Promise<string> {
  const options: RequestInit = { headers: authHeaders(), cache: "no-store" };
  if (signal) options.signal = signal;
  const res = await fetch(getUrl(path), options);
  if (!res.ok) throw await parseError(res);
  return res.text();
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
  const res = await fetch(getUrl(path), {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
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

async function parseError(res: Response): Promise<ApiError> {
  if (res.status === 401) emitUnauthorized();
  const text = await res.text().catch(() => "Unknown error");
  return new ApiError(res.status, errorCode(res.status, text), text);
}

export async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) {
    // SAFETY: a 204 carries no body at all, so `undefined` is the only value
    // this branch can produce (`res.json()` would throw on the empty payload).
    // The endpoints that answer 204 are the void ones, whose callers declare
    // `T` as `void`/`undefined`.
    return undefined as T;
  }
  // `Response.json()` is typed `Promise<any>` by lib.dom, so `T` — the response
  // contract the calling wrapper in `src/lib/api` declares for this endpoint —
  // flows through without an assertion.
  return res.json();
}

export async function expectOk(res: Response): Promise<void> {
  if (!res.ok) throw await parseError(res);
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

// ---------------------------------------------------------------------------
// In-memory GET cache (browser only).
//
// Short TTL so back-navigation and repeat renders reuse the last response
// instead of flashing a loading state; any mutation through this module
// invalidates the whole cache, so staleness is bounded to TTL for changes
// made outside this tab.
// ---------------------------------------------------------------------------

const CACHE_TTL_MS = 30_000;

const responseCache = new Map<string, { value: unknown; expires: number }>();
const inflight = new Map<string, Promise<unknown>>();

/**
 * Bust caches after a mutation. Pass the mutated `path` for keyed,
 * resource-scoped query invalidation (the good-practice path); omit it for a
 * blanket invalidation (manual callers that don't know the affected resource).
 * Either way the legacy in-memory GET cache is fully cleared — it's a 30s TTL
 * map, so dropping it wholesale is cheap and keeps it coherent.
 */
export function invalidateApiCache(path?: string): void {
  responseCache.clear();
  inflight.clear();
  if (path === undefined) {
    queryClient.invalidateQueries();
  } else {
    invalidateQueriesForPath(path);
  }
}

if (isBrowser()) {
  // Login/logout changes identity — drop all cached data (not just invalidate)
  // so the previous user's reads can't linger under RBAC.
  window.addEventListener("printstash:auth-changed", () => {
    responseCache.clear();
    inflight.clear();
    queryClient.clear();
  });
}

export interface GetJsonOptions {
  /** Bypass the in-memory cache (polling endpoints, explicit refresh). */
  fresh?: boolean;
}

export async function getJson<T>(path: string, options?: GetJsonOptions): Promise<T> {
  if (!isBrowser() || options?.fresh) {
    const res = await fetch(getUrl(path), {
      headers: authHeaders(),
      cache: "no-store",
    });
    return handleResponse<T>(res);
  }

  const now = Date.now();
  const cached = responseCache.get(path);
  if (cached && cached.expires > now) {
    // SAFETY: the cache is keyed by request path and is only written below, with
    // the body `handleResponse<T>` just produced for that same path — so a live
    // entry for `path` holds exactly what a cache miss would have returned.
    return cached.value as T;
  }
  const pending = inflight.get(path);
  if (pending) {
    // SAFETY: the in-flight entry for `path` is the promise created below for
    // that same path, i.e. `handleResponse<T>` on this endpoint's response;
    // sharing it is what makes concurrent readers issue one request.
    return pending as Promise<T>;
  }
  const request = (async () => {
    const res = await fetch(getUrl(path), {
      headers: authHeaders(),
      cache: "no-store",
    });
    const value = await handleResponse<T>(res);
    responseCache.set(path, { value, expires: Date.now() + CACHE_TTL_MS });
    return value;
  })();
  inflight.set(path, request);
  try {
    return await request;
  } finally {
    inflight.delete(path);
  }
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
): Promise<T> {
  const res = await fetch(getUrl(path), {
    method,
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  const value = await handleResponse<T>(res);
  invalidateApiCache(path);
  return value;
}

export async function sendForm<T>(path: string, formData: FormData): Promise<T> {
  const res = await fetch(getUrl(path), {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  const value = await handleResponse<T>(res);
  invalidateApiCache(path);
  return value;
}

export async function sendAction(path: string, method: "POST" | "DELETE"): Promise<void> {
  const res = await fetch(getUrl(path), {
    method,
    headers: authHeaders(),
  });
  await expectOk(res);
  invalidateApiCache(path);
}
