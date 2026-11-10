"use client";

/** Internal anchors retain native gestures while TanStack owns same-tab history. */
import { Link as RouterLink, type HistoryState } from "@tanstack/react-router";
import type { AnchorHTMLAttributes, ReactNode } from "react";
import { parseAppSearch } from "@/lib/router-search";

export type LinkProps = {
  href: string;
  children?: ReactNode;
  replace?: boolean;
  state?: HistoryState;
} & Omit<AnchorHTMLAttributes<HTMLAnchorElement>, "href">;

export function Link({ href, replace, state, ...rest }: LinkProps) {
  const target = new URL(href, window.location.origin);
  if (target.origin !== window.location.origin) return <a href={href} {...rest} />;
  return (
    <RouterLink
      to={target.pathname}
      search={parseAppSearch(target.search)}
      hash={target.hash.slice(1)}
      replace={replace}
      state={state}
      preload={false}
      {...rest}
    />
  );
}
