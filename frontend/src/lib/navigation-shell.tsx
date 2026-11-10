import { createContext, useContext } from "react";

interface NavigationShellState {
  savedViewsRequested: boolean;
  savedViewsOpen: boolean;
  savedViewsHost: HTMLDivElement | null;
  savedViewsAvailable: boolean;
  attachSavedViews: (node: HTMLDivElement | null) => void;
  setSavedViewsAvailable: (available: boolean) => void;
  requestSavedViews: () => void;
  consumeSavedViewsRequest: () => void;
  setSavedViewsOpen: (open: boolean) => void;
}

export const NavigationShellContext = createContext<NavigationShellState>({
  savedViewsRequested: false,
  savedViewsOpen: false,
  savedViewsHost: null,
  savedViewsAvailable: false,
  attachSavedViews: () => {},
  setSavedViewsAvailable: () => {},
  requestSavedViews: () => {},
  consumeSavedViewsRequest: () => {},
  setSavedViewsOpen: () => {},
});

export function useNavigationShell() {
  return useContext(NavigationShellContext);
}
