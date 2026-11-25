import { createContext, useContext } from "react";

export interface CollectionsPaneState {
  host: HTMLDivElement | null;
  attach: (node: HTMLDivElement | null) => void;
  breadcrumbHost: HTMLDivElement | null;
  attachBreadcrumbs: (node: HTMLDivElement | null) => void;
  open: boolean;
  width: number;
  setWidth: (width: number) => void;
  toggle: () => void;
  mode: "collections" | "search" | "pending" | "views" | "settings";
  showCollections: () => void;
  showSearch: () => void;
  showPending: () => void;
  showSavedViews: () => void;
  showSettings: () => void;
}

export const CollectionsPaneContext = createContext<CollectionsPaneState | null>(null);

export const CollectionsPanePresenceContext = createContext(false);

export function useHasCollectionsPane() {
  return useContext(CollectionsPanePresenceContext);
}

/** Sidebar visibility for controls that update when it opens or closes. */
export const CollectionsPaneOpenContext = createContext<boolean | null>(null);

export function useCollectionsPaneOpen() {
  return useContext(CollectionsPaneOpenContext);
}

/** Standalone feature renders keep their existing inline sidebar. */
export function useCollectionsPane() {
  return useContext(CollectionsPaneContext);
}
