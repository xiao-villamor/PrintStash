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
  Link as RouterLink,
  useLocation,
  useNavigate,
  useSearchParams as useRouterSearchParams,
} from "react-router-dom";
import type { AnchorHTMLAttributes, ReactNode } from "react";

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
  return {
    push: (href) => navigate(href),
    replace: (href) => navigate(href, { replace: true }),
    back: () => navigate(-1),
    forward: () => navigate(1),
    refresh: () => {},
    prefetch: () => {},
  };
}

export function usePathname(): string {
  return useLocation().pathname;
}

export function useSearchParams(): URLSearchParams {
  const [params] = useRouterSearchParams();
  return params;
}

type LinkProps = {
  href: string;
  children: ReactNode;
  /** Accepted for API parity with next/link; ignored under React Router. */
  prefetch?: boolean;
  scroll?: boolean;
  replace?: boolean;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

export function Link({ href, prefetch: _p, scroll: _s, replace, ...rest }: LinkProps) {
  return <RouterLink to={href} replace={replace} {...rest} />;
}
