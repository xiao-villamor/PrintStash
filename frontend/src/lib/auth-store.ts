/**
 * Browser auth metadata store. Access JWTs live only in an HttpOnly cookie.
 *
 * The React AuthContext and API client consume this module instead of
 * touching localStorage directly. Events drive cross-component sync.
 */

const TOKEN_KEY = "printstash.token";
const USER_KEY = "printstash.user";
const LEGACY_TOKEN_KEY = "nexus3d.token";
const LEGACY_USER_KEY = "nexus3d.user";
const AUTH_EVENT = "printstash:auth-changed";
const UNAUTH_EVENT = "printstash:unauthorized";
const SESSION_EXPIRED_KEY = "printstash.session-expired";

export interface StoredUser {
  id: number;
  username: string;
  email: string | null;
  is_superuser: boolean;
}

function isBrowser(): boolean {
  return "window" in globalThis;
}

function emit() {
  if (isBrowser()) window.dispatchEvent(new Event(AUTH_EVENT));
}

export function getToken(): string | null {
  return null;
}

/**
 * Decode the persisted user blob. localStorage is writable by anything running
 * on the origin, so the stored JSON is a foreign document until decoded here;
 * a blob missing the identity fields yields null rather than a half-built user.
 */
function parseStoredUser(raw: string): StoredUser | null {
  const candidate: unknown = JSON.parse(raw);
  if (!(candidate instanceof Object) || !("id" in candidate) || !("username" in candidate)) {
    return null;
  }
  const id = Number(candidate.id);
  if (!Number.isInteger(id)) return null;
  return {
    id,
    username: String(candidate.username),
    email: "email" in candidate && candidate.email !== null ? String(candidate.email) : null,
    is_superuser: "is_superuser" in candidate && candidate.is_superuser === true,
  };
}

export function getUser(): StoredUser | null {
  if (!isBrowser()) return null;
  try {
    const raw = localStorage.getItem(USER_KEY) ?? localStorage.getItem(LEGACY_USER_KEY);
    return raw ? parseStoredUser(raw) : null;
  } catch {
    return null;
  }
}

export function isLoggedIn(): boolean {
  return !!getUser();
}

export function storeLogin(token: string, user: StoredUser, options?: { silent?: boolean }): void {
  if (!isBrowser()) return;
  try {
    // The backend also sets the access JWT as an HttpOnly SameSite cookie.
    // Never persist the response token where injected JavaScript can read it.
    void token;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.setItem(USER_KEY, JSON.stringify(user));
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_USER_KEY);
    // `silent` persists the latest user without broadcasting an identity
    // change. The auth-changed event makes the API layer wipe the whole query
    // cache (so one user's data never leaks to the next); the bootstrap/refresh
    // getMe is the *same* identity, so firing it there would needlessly nuke
    // freshly-loaded queries on every page load.
    if (!options?.silent) emit();
  } catch {
    /* ignore */
  }
}

export function clearLogin(): void {
  if (!isBrowser()) return;
  try {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(LEGACY_TOKEN_KEY);
    localStorage.removeItem(LEGACY_USER_KEY);
    emit();
  } catch {
    /* ignore */
  }
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
  if (!isBrowser()) return;
  // Login failures also return 401. Only expire an established session, and
  // clear it before broadcasting so concurrent failed requests become no-ops.
  if (!isLoggedIn()) return;
  try {
    sessionStorage.setItem(SESSION_EXPIRED_KEY, "1");
  } catch {
    /* ignore */
  }
  clearLogin();
  window.dispatchEvent(new Event(UNAUTH_EVENT));
}

export function consumeSessionExpired(): boolean {
  if (!isBrowser()) return false;
  try {
    const expired = sessionStorage.getItem(SESSION_EXPIRED_KEY) === "1";
    sessionStorage.removeItem(SESSION_EXPIRED_KEY);
    return expired;
  } catch {
    return false;
  }
}

export function onUnauthorized(cb: () => void): () => void {
  if (!isBrowser()) return () => {};
  const handler = () => cb();
  window.addEventListener(UNAUTH_EVENT, handler);
  return () => window.removeEventListener(UNAUTH_EVENT, handler);
}
