import { Suspense } from "react";
import { Outlet } from "@tanstack/react-router";

/** The public route also keeps its deferred screen inside a recovery boundary. */
export function RouteRoot() {
  return (
    <Suspense fallback={<div className="min-h-screen w-full bg-background" aria-busy="true" />}>
      <Outlet />
    </Suspense>
  );
}
