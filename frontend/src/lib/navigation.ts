"use client";

/**
 * Navigation shim backed by React Router.
 *
 * Mirrors the small slice of the `next/navigation` + `next/link` API the app
 * actually used, so the migration off Next is mostly an import-path swap:
 *   `next/navigation` / `next/link` → `@/lib/navigation`.
 *
 * `useSearchParams` returns the read-only `URLSearchParams` directly (matching
 * Next's shape), so components that build a query string by hand and then call
 * `router.replace(...)` keep working unchanged.
 */

import {
  useLocation,
  useNavigate,
  useSearchParams as useRouterSearchParams,
} from "react-router-dom";
import { useMemo } from "react";

/** Next's `NavigateOptions` (e.g. `{ scroll: false }`) — accepted, ignored. */
interface NavOptions {
  scroll?: boolean;
}

export interface AppRouter {
  push: (href: string, options?: NavOptions) => void;
  replace: (href: string, options?: NavOptions) => void;
  back: () => void;
  forward: () => void;
  /** Next's server-data refresh has no analogue in a client SPA — no-op. */
  refresh: () => void;
  prefetch: (href: string) => void;
}

export function useRouter(): AppRouter {
  const navigate = useNavigate();
  return useMemo(
    () => ({
      push: (href: string) => navigate(href),
      replace: (href: string) => navigate(href, { replace: true }),
      back: () => navigate(-1),
      forward: () => navigate(1),
      refresh: () => {},
      prefetch: () => {},
    }),
    [navigate],
  );
}

export function usePathname(): string {
  return useLocation().pathname;
}

export function useSearchParams(): URLSearchParams {
  const [params] = useRouterSearchParams();
  return params;
}
