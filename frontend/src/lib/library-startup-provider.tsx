import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useMediaQuery } from "@/lib/use-media-query";
import {
  LibraryStartupContext,
  type SecondaryRead,
  type StartupOutcome,
  type StartupPart,
} from "@/lib/library-startup-context";
import { markStartup } from "@/lib/startup-timing";

type PartState = "pending" | StartupOutcome;

/** Release auxiliary reads after the first usable library frame, or on demand. */
export function LibraryStartupProvider({
  children,
  active,
}: {
  children: ReactNode;
  active: boolean;
}) {
  const desktop = useMediaQuery("(min-width: 768px)");
  const [cards, setCards] = useState<PartState>("pending");
  const [tree, setTree] = useState<PartState>("pending");
  const [painted, setPainted] = useState(!active);
  const [requested, setRequested] = useState<ReadonlySet<SecondaryRead>>(() => new Set());
  const settled = cards !== "pending" && (!desktop || tree !== "pending");
  const failed = cards === "failed" || tree === "failed";
  const settle = useCallback((part: StartupPart, outcome: StartupOutcome) => {
    if (outcome === "ready") markStartup(part === "cards" ? "library-cards" : "library-tree");
    const update = part === "cards" ? setCards : setTree;
    update((current) => (current === "pending" ? outcome : current));
  }, []);
  const request = useCallback((read: SecondaryRead) => {
    setRequested((current) => (current.has(read) ? current : new Set([...current, read])));
  }, []);

  useEffect(() => {
    if (!active || painted || (!settled && !failed)) return;
    let second: number | null = null;
    const first = requestAnimationFrame(() => {
      second = requestAnimationFrame(() => {
        if (!failed) markStartup("library-ready");
        setPainted(true);
      });
    });
    return () => {
      cancelAnimationFrame(first);
      if (second !== null) cancelAnimationFrame(second);
    };
  }, [active, painted, settled, failed]);

  const value = useMemo(
    () => ({
      canLoad: (read: SecondaryRead) => !active || painted || requested.has(read),
      request,
      settle,
    }),
    [active, painted, requested, request, settle],
  );
  return <LibraryStartupContext.Provider value={value}>{children}</LibraryStartupContext.Provider>;
}
