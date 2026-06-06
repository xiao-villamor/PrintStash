import { NextRequest, NextResponse } from "next/server";

/**
 * Server-side setup gate.
 *
 * On every page request (not API, not static assets) we probe
 * ``GET /api/v1/setup/status`` once and cache the result in a short-lived
 * cookie.  If the vault is unconfigured the browser is immediately redirected
 * to ``/setup`` — no SSR flash of the empty app shell.
 *
 * Routes that MUST be reachable while unconfigured:
 *   - /setup         (the wizard itself)
 *   - /api/v1/*      (backend calls — proxied via rewrites)
 *   - /_next/*       (static bundles, JS, CSS)
 *   - /favicon.ico   (browser tab icon)
 */

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SETUP_COOKIE = "printstash.setup";
const LEGACY_SETUP_COOKIE = "nexus3d.setup";

export async function proxy(request: NextRequest) {
  // Check the short-lived cookie first to avoid hitting the API on every nav.
  const cookie =
    request.cookies.get(SETUP_COOKIE) ?? request.cookies.get(LEGACY_SETUP_COOKIE);
  if (cookie?.value === "1") {
    return NextResponse.next();
  }

  // Probe the backend.
  try {
    const res = await fetch(`${API_BASE}/api/v1/setup/status`, {
      signal: AbortSignal.timeout(3000),
    });
    if (!res.ok) {
      return NextResponse.next();
    }
    const status = await res.json();

    if (!status.configured) {
      const redirect = NextResponse.redirect(new URL("/setup", request.url));
      redirect.cookies.set(SETUP_COOKIE, "0", { maxAge: 30, path: "/" });
      return redirect;
    }

    // Vault is configured — cache for 1 minute.
    const response = NextResponse.next();
    response.cookies.set(SETUP_COOKIE, "1", { maxAge: 60, path: "/" });
    return response;
  } catch {
    return NextResponse.next();
  }
}

export const config = {
  matcher: [
    /*
     * Match all page routes EXCEPT:
     * - /setup, /login (the wizard and auth pages)
     * - /api/* (backend proxy)
     * - /_next/* (static bundles, images)
     * - /favicon.ico
     */
    "/((?!setup|login|api|_next|favicon.ico).*)",
  ],
};
