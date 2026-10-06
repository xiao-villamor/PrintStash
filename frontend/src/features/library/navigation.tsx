import { Link, useLocation, useNavigate, type LinkProps } from "react-router-dom";
import { historyIndex, knownOrigin, type LibraryEntry } from "./navigation-state";

type LibraryLinkProps = Omit<LinkProps, "to" | "state"> & { href: string };

/** Native links retain a deep-link fallback; same-tab navigation also names its source entry. */
export function LibraryItemLink({
  href,
  origin,
  ...props
}: LibraryLinkProps & { origin?: LibraryEntry }) {
  const location = useLocation();
  const target = new URL(href, window.location.origin);
  if (origin) target.searchParams.set("return", origin.href);
  return (
    <Link
      {...props}
      to={`${target.pathname}${target.search}${target.hash}`}
      state={origin && location.key === origin.key ? { libraryOrigin: origin.key } : null}
      data-library-entry={target.pathname}
    />
  );
}

/** A return URL is a Library view, never an arbitrary redirect target. */
function libraryReturn(search: string, fallback: string): string {
  const value = new URLSearchParams(search).get("return");
  if (!value || !value.startsWith("/") || value.startsWith("//")) return fallback;
  const target = new URL(value, window.location.origin);
  return target.origin === window.location.origin && target.pathname === "/"
    ? `${target.pathname}${target.search}${target.hash}`
    : fallback;
}

/** Reuse the immediate source entry; a replaced/unknown entry uses the safe URL fallback. */
export function LibraryBackLink({ href: fallback, onClick, ...props }: LibraryLinkProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const href = libraryReturn(location.search, fallback);
  return (
    <Link
      {...props}
      to={href}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey ||
          (props.target && props.target !== "_self")
        )
          return;
        const origin = knownOrigin(location.state);
        const index = historyIndex();
        if (origin && origin.href === href && origin.index !== null && index === origin.index + 1) {
          event.preventDefault();
          navigate(-1);
        }
      }}
    />
  );
}
