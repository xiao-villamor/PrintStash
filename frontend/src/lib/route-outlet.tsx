import { Suspense, useLayoutEffect, useState } from "react";
import { Outlet, useRouterState } from "@tanstack/react-router";
import { usePathname, useSearchParams } from "./navigation";
import { useContentMotion } from "./use-content-motion";

type MainSection = "search" | "favorites" | "settings" | "other";

function normalizePath(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

/** Inside Suspense: motion begins only when the destination actually commits,
 * including its first lazy load. Queries continue to own their loading states. */
function ContentReady({
  section,
  onReady,
}: {
  section: MainSection;
  onReady: (section: MainSection) => void;
}) {
  useLayoutEffect(() => onReady(section), [section, onReady]);
  return null;
}

/** Retire outgoing page effects while its lazy destination is being matched. */
export function RouteOutlet() {
  const pathname = normalizePath(usePathname());
  const params = useSearchParams();
  const section: MainSection =
    pathname === "/search"
      ? "search"
      : pathname === "/" && params.get("favorites") === "true"
        ? "favorites"
        : pathname === "/settings"
          ? "settings"
          : "other";
  const [committedSection, setCommittedSection] = useState(section);
  const changingPath = useRouterState({
    select: (state) => {
      const active = state.matches.at(-1);
      return (
        active !== undefined &&
        normalizePath(active.pathname) !== normalizePath(state.location.pathname)
      );
    },
  });
  const motion = useContentMotion(committedSection, {
    enabled: section !== "other",
    animateOnEnable: true,
  });
  const pending = <div className="min-h-screen w-full bg-background" aria-busy="true" />;
  // Retire old URL-writing effects during a path change, but keep this surface
  // mounted so rapid navigation inherits its presentation.
  // Chrome and portalled sidebar navigation live outside its DOM subtree.
  return (
    <div
      ref={motion}
      className="workbench-main-content h-full min-h-0 w-full min-w-0"
      data-main-section={committedSection}
    >
      {changingPath ? (
        pending
      ) : (
        <Suspense fallback={pending}>
          <Outlet />
          <ContentReady section={section} onReady={setCommittedSection} />
        </Suspense>
      )}
    </div>
  );
}
