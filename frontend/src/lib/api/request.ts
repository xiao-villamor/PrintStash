import { emitUnauthorized, getStoredToken } from "@/lib/auth";
import { ApiError } from "@/lib/errors";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "";

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

export async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(getUrl(path), {
    headers: authHeaders(),
    cache: "no-store",
  });
  return handleResponse<T>(res);
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
  return expectOk(res);
}
