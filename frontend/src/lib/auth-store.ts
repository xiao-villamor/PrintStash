/**
 * Single module that owns all localStorage reads and writes for auth state.
 *
 * The React AuthContext and API client consume this module instead of
 * touching localStorage directly. Events drive cross-component sync.
 */

const TOKEN_KEY = "printstash.token";
const USER_KEY = "printstash.user";
const API_KEY_KEY = "printstash.apiKey";
const LEGACY_TOKEN_KEY = "nexus3d.token";
const LEGACY_USER_KEY = "nexus3d.user";
const LEGACY_API_KEY_KEY = "nexus3d.apiKey";
const AUTH_EVENT = "printstash:auth-changed";
const UNAUTH_EVENT = "printstash:unauthorized";

export interface StoredUser {
  id: number;
  username: string;
  email: string | null;
  is_superuser: boolean;
}

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

function emit() {
  if (isBrowser()) window.dispatchEvent(new Event(AUTH_EVENT));
}

export function getToken(): string | null {
  if (!isBrowser()) return null;
  try {
    return localStorage.getItem(TOKEN_KEY) ?? localStorage.getItem(LEGACY_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function getUser(): StoredUser | null {
  if (!isBrowser()) return null;
  try {
    const raw = localStorage.getItem(USER_KEY) ?? localStorage.getItem(LEGACY_USER_KEY);
    return raw ? (JSON.parse(raw) as StoredUser) : null;
  } catch {
    return null;
  }
}

export function isLoggedIn(): boolean {
  return !!getToken();
}

export function getApiKey(): string | null {
  if (!isBrowser()) return null;
  try {
    return localStorage.getItem(API_KEY_KEY) ?? localStorage.getItem(LEGACY_API_KEY_KEY);
  } catch {
    return null;
  }
}

export function setApiKey(key: string | null): void {
  if (!isBrowser()) return;
  try {
    if (key && key.trim()) {
      localStorage.setItem(API_KEY_KEY, key.trim());
      localStorage.removeItem(LEGACY_API_KEY_KEY);
    } else {
      localStorage.removeItem(API_KEY_KEY);
      localStorage.removeItem(LEGACY_API_KEY_KEY);
    }
    emit();
  } catch { /* ignore */ }
}

export function storeLogin(token: string, user: StoredUser): void {
  if (!isBrowser()) return;
  try {
    localStorage.setItem(TOKEN_KEY, token);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_USER_KEY);
    emit();
  } catch { /* ignore */ }
}

export function clearLogin(): void {
  if (!isBrowser()) return;
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_USER_KEY);
    emit();
  } catch { /* ignore */ }
}

export function onAuthChange(cb: () => void): () => void {
  if (!isBrowser()) return () => {};
  const handler = () => cb();
  window.addEventListener(AUTH_EVENT, handler);
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener(AUTH_EVENT, handler);
    window.removeEventListener("storage", handler);
  };
}

export function emitUnauthorized(): void {
  if (isBrowser()) window.dispatchEvent(new Event(UNAUTH_EVENT));
}

export function onUnauthorized(cb: () => void): () => void {
  if (!isBrowser()) return () => {};
  const handler = () => cb();
  window.addEventListener(UNAUTH_EVENT, handler);
  return () => window.removeEventListener(UNAUTH_EVENT, handler);
}
