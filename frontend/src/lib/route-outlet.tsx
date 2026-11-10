import { Suspense } from "react";
import { Outlet, useRouterState } from "@tanstack/react-router";

function normalizePath(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

/** Retire outgoing page effects while its lazy destination is being matched. */
export function RouteOutlet() {
  const changingPath = useRouterState({
    select: (state) => {
      const active = state.matches.at(-1);
      return (
        active !== undefined &&
        normalizePath(active.pathname) !== normalizePath(state.location.pathname)
      );
    },
  });
  const pending = <div className="min-h-screen w-full bg-background" aria-busy="true" />;
  // TanStack retains prior matches during loading. Their URL-writing effects
  // must not consume the requested destination's search parameters.
  if (changingPath) return pending;
  return (
    <Suspense fallback={pending}>
      <Outlet />
    </Suspense>
  );
}
