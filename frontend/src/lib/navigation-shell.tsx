import { createContext, useContext } from "react";

export type SavedViewsRequest = { kind: "rail" } | { kind: "sidebar"; anchor: HTMLElement };

interface NavigationShellState {
  savedViewsRequest: SavedViewsRequest | null;
  savedViewsOpen: boolean;
  savedViewsHost: HTMLDivElement | null;
  savedViewsAvailable: boolean;
  attachSavedViews: (node: HTMLDivElement | null) => void;
  setSavedViewsAvailable: (available: boolean) => void;
  requestSavedViews: (anchor?: HTMLElement) => void;
  consumeSavedViewsRequest: () => void;
  setSavedViewsOpen: (open: boolean) => void;
}

export const NavigationShellContext = createContext<NavigationShellState>({
  savedViewsRequest: null,
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
