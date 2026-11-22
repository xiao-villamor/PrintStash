/**
 * Requests to the PrintStash server carry the browser's cookies for that host.
 *
 * A server published behind a sign-in proxy (Cloudflare Access, Authelia,
 * Authentik, oauth2-proxy…) only lets a request through with the proxy's own
 * session cookie, which the user gets by signing in once in a normal tab.
 * PrintStash itself never authenticates the extension by cookie: every
 * authenticated call sends an explicit Bearer credential, which the server
 * prefers over any session cookie.
 */
export const vaultFetch: typeof fetch = (input, init) =>
  fetch(input, { ...init, credentials: init?.credentials ?? "include" });
