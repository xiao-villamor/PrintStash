"use client";

/**
 * `next/link` shim backed by React Router, split out from `@/lib/navigation`
 * so the navigation hooks live in a component-free module.
 */

import { Link as RouterLink } from "react-router-dom";
import type { AnchorHTMLAttributes, ReactNode } from "react";

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
