import { useCallback, useMemo, useState, type ReactNode } from "react";
import { NavigationShellContext, type SavedViewsRequest } from "./navigation-shell";

/** Carries a rail request across routes without moving ownership of saved views. */
export function NavigationShellProvider({ children }: { children: ReactNode }) {
  const [savedViewsRequest, setRequested] = useState<SavedViewsRequest | null>(null);
  const [savedViewsOpen, setSavedViewsOpen] = useState(false);
  const [savedViewsHost, attachSavedViews] = useState<HTMLDivElement | null>(null);
  const [savedViewsAvailable, setSavedViewsAvailable] = useState(false);
  const requestSavedViews = useCallback((anchor?: HTMLElement) => {
    setRequested((current) => {
      if (!anchor) return { kind: "rail" };
      if (current?.kind === "sidebar" && current.anchor === anchor) return null;
      return { kind: "sidebar", anchor };
    });
  }, []);
  const consumeSavedViewsRequest = useCallback(() => setRequested(null), []);
  const value = useMemo(
    () => ({
      savedViewsRequest,
      savedViewsOpen,
      savedViewsHost,
      savedViewsAvailable,
      attachSavedViews,
      setSavedViewsAvailable,
      requestSavedViews,
      consumeSavedViewsRequest,
      setSavedViewsOpen,
    }),
    [
      savedViewsRequest,
      savedViewsOpen,
      savedViewsHost,
      savedViewsAvailable,
      requestSavedViews,
      consumeSavedViewsRequest,
    ],
  );
  return (
    <NavigationShellContext.Provider value={value}>{children}</NavigationShellContext.Provider>
  );
}
