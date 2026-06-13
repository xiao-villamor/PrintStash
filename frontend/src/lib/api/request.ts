import { emitUnauthorized, getStoredToken } from "@/lib/auth";
import { ApiError } from "@/lib/errors";
import { queryClient } from "@/lib/query-client";

const API_BASE = import.meta.env.VITE_API_URL || "";
const WS_BASE = import.meta.env.VITE_WS_URL || "";

function isBrowser(): boolean {
  return typeof window !== "undefined";
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
  const res = await fetch(getUrl(path), {
    headers: authHeaders(),
    cache: "force-cache",
  });
  if (!res.ok) throw await parseError(res);
  return res.blob();
}

/**
 * Download a protected file. Plain <a href> links can't carry the bearer
 * token, so reads gated behind auth (post-RBAC) 401. Fetch the blob with the
 * token, then trigger a save via a temporary object URL.
 */
export async function downloadAuthenticatedFile(
  path: string,
  filename?: string,
): Promise<void> {
  const res = await fetch(getUrl(path), {
    headers: authHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw await parseError(res);
  const blob = await res.blob();
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  if (filename) a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export function getWsUrl(path: string): string {
  if (!isBrowser()) {
    const base = (WS_BASE || API_BASE || "http://localhost:8000").replace(
      /\/$/,
      "",
    );
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

function errorCode(status: number, body: string): string {
  try {
    const parsed = JSON.parse(body);
    return typeof parsed?.detail === "string" ? parsed.detail : String(status);
  } catch {
    return String(status);
  }
}

async function parseError(res: Response): Promise<ApiError> {
  if (res.status === 401) emitUnauthorized();
  const text = await res.text().catch(() => "Unknown error");
  return new ApiError(res.status, errorCode(res.status, text), text);
}

export async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) throw await parseError(res);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

export async function expectOk(res: Response): Promise<void> {
  if (!res.ok) throw await parseError(res);
}

export function authHeaders(): Record<string, string> {
  const headers: Record<string, string> = {};
  const token = getStoredToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

export function jsonHeaders(): Record<string, string> {
  return { "Content-Type": "application/json", ...authHeaders() };
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

export function invalidateApiCache(): void {
  responseCache.clear();
  inflight.clear();
  // Any mutation through this module busts the TanStack Query cache too, so
  // useQuery-backed views (collections, tags, …) refetch and stay coherent
  // with the legacy in-memory cache during the incremental migration.
  queryClient.invalidateQueries();
}

if (isBrowser()) {
  // A login/logout changes what reads may return — drop everything.
  window.addEventListener("printstash:auth-changed", invalidateApiCache);
}

export interface GetJsonOptions {
  /** Bypass the in-memory cache (polling endpoints, explicit refresh). */
  fresh?: boolean;
}

export async function getJson<T>(
  path: string,
  options?: GetJsonOptions,
): Promise<T> {
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
    return cached.value as T;
  }
  const pending = inflight.get(path);
  if (pending) {
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
  body: unknown,
): Promise<T> {
  const res = await fetch(getUrl(path), {
    method,
    headers: jsonHeaders(),
    body: JSON.stringify(body),
  });
  invalidateApiCache();
  return handleResponse<T>(res);
}

export async function sendForm<T>(
  path: string,
  formData: FormData,
): Promise<T> {
  const res = await fetch(getUrl(path), {
    method: "POST",
    headers: authHeaders(),
    body: formData,
  });
  invalidateApiCache();
  return handleResponse<T>(res);
}

export async function sendAction(
  path: string,
  method: "POST" | "DELETE",
): Promise<void> {
  const res = await fetch(getUrl(path), {
    method,
    headers: authHeaders(),
  });
  invalidateApiCache();
  return expectOk(res);
}
