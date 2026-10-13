import { useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from "react";
import { useLocation } from "react-router-dom";
import { useMediaQuery } from "@/lib/use-media-query";
import { useAuth } from "@/lib/auth-context";
import {
  LibraryStartupContext,
  type SecondaryRead,
  type StartupOutcome,
  type StartupPart,
} from "@/lib/library-startup-context";
import { markStartup } from "@/lib/startup-timing";

type PartState = StartupOutcome;
interface NavigationState {
  key: string;
  id: number;
  started: number;
  parts: Record<StartupPart, PartState>;
  usable: boolean;
  complete: boolean;
  requested: Set<SecondaryRead>;
}
let sequence = 0;
function navigation(key: string): NavigationState {
  return {
    key,
    id: ++sequence,
    started: performance.now(),
    parts: { cards: "pending", tree: "idle", media: "pending" },
    usable: false,
    complete: false,
    requested: new Set<SecondaryRead>(),
  };
}

/** Each history destination owns its readiness; callbacks cannot settle its successor. */
export function LibraryStartupProvider({
  children,
  active,
}: {
  children: ReactNode;
  active: boolean;
}) {
  const location = useLocation();
  const auth = useAuth();
  const desktop = useMediaQuery("(min-width: 768px)");
  const key = `${location.key}:${active}`;
  const [state, setState] = useState(() => navigation(key));
  if (state.key !== key) setState(navigation(key));
  const id = state.id;
  const failed = Object.values(state.parts).includes("failed");
  const contentReady = state.parts.cards === "ready" && (!desktop || state.parts.tree === "ready");
  const allReady =
    Object.values(state.parts).every((part) => part === "ready") &&
    !auth.loading &&
    auth.user !== null;
  const settle = useCallback(
    (part: StartupPart, outcome: StartupOutcome) => {
      setState((current) =>
        current.id !== id || current.parts[part] === outcome
          ? current
          : { ...current, parts: { ...current.parts, [part]: outcome }, complete: false },
      );
    },
    [id],
  );
  const request = useCallback(
    (read: SecondaryRead) => {
      setState((current) =>
        current.id !== id || current.requested.has(read)
          ? current
          : { ...current, requested: new Set([...current.requested, read]) },
      );
    },
    [id],
  );

  // Concurrent renders may prepare generations in a different order from commits.
  // Consumers must identify the committed history destination, not the largest id
  // or the most recently prepared start timestamp.
  useLayoutEffect(() => {
    performance.mark("printstash:navigation:current", {
      detail: { navigation: id, historyKey: location.key, active },
    });
  }, [id, location.key, active]);

  useEffect(() => {
    if (!active) return;
    const record = (phase: string, startTime?: number) => {
      const name = `printstash:navigation:${id}:${phase}`;
      if (performance.getEntriesByName?.(name).length === 0)
        performance.mark(name, { startTime, detail: { navigation: id } });
    };
    record("start", state.started);
    if (!auth.loading && auth.user !== null) record("session-validated");
    for (const [part, outcome] of Object.entries(state.parts)) {
      if (outcome === "ready") record(part);
    }
    if (state.parts.cards === "ready") markStartup("library-cards");
    if (state.parts.tree === "ready") markStartup("library-tree");
    if (failed) record("failed");
    if ((!contentReady || state.usable) && (!allReady || state.complete)) return;
    let second: number | null = null;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        if (contentReady && !state.usable) {
          record("usable");
          markStartup("library-ready");
        }
        if (allReady && !state.complete) record("complete");
        setState((current) =>
          current.id !== id
            ? current
            : {
                ...current,
                usable: current.usable || contentReady,
                complete: allReady,
              },
        );
      });
    });
    return () => {
      cancelAnimationFrame(first);
      if (second !== null) cancelAnimationFrame(second);
    };
  }, [
    active,
    id,
    state.started,
    state.parts,
    state.usable,
    state.complete,
    auth.loading,
    auth.user,
    contentReady,
    allReady,
    failed,
  ]);

  const mobileIdle =
    !desktop && state.usable && state.parts.media === "ready" && state.parts.tree === "idle";
  const value = useMemo(
    () => ({
      complete: !active || state.complete,
      canLoad: (read: SecondaryRead) =>
        !active || state.complete || mobileIdle || failed || state.requested.has(read),
      request,
      settle,
    }),
    [active, state.complete, state.requested, mobileIdle, failed, request, settle],
  );
  return <LibraryStartupContext.Provider value={value}>{children}</LibraryStartupContext.Provider>;
}
