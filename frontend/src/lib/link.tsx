"use client";

/**
 * Internal links use React Router history. Navigation hooks live separately
 * in the component-free navigation module.
 */

import { Link as RouterLink } from "react-router-dom";
import type { AnchorHTMLAttributes, ReactNode } from "react";

type LinkProps = {
  href: string;
  children: ReactNode;
  replace?: boolean;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

export function Link({ href, replace, ...rest }: LinkProps) {
  return <RouterLink to={href} replace={replace} {...rest} />;
}
