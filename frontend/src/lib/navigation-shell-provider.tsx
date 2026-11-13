import { useCallback, useMemo, useState, type ReactNode } from "react";
import { NavigationShellContext } from "./navigation-shell";

/** Carries a rail request across routes without moving ownership of saved views. */
export function NavigationShellProvider({ children }: { children: ReactNode }) {
  const [savedViewsRequested, setRequested] = useState(false);
  const [savedViewsOpen, setSavedViewsOpen] = useState(false);
  const [savedViewsHost, attachSavedViews] = useState<HTMLDivElement | null>(null);
  const [savedViewsAvailable, setSavedViewsAvailable] = useState(false);
  const requestSavedViews = useCallback(() => setRequested(true), []);
  const consumeSavedViewsRequest = useCallback(() => setRequested(false), []);
  const value = useMemo(
    () => ({
      savedViewsRequested,
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
      savedViewsRequested,
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
