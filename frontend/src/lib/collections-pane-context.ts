import { createContext, useContext } from "react";

export interface CollectionsPaneState {
  host: HTMLDivElement | null;
  attach: (node: HTMLDivElement | null) => void;
  breadcrumbHost: HTMLDivElement | null;
  attachBreadcrumbs: (node: HTMLDivElement | null) => void;
  open: boolean;
  toggle: () => void;
  mode: "collections" | "search" | "favorites" | "pending" | "views";
  showCollections: () => void;
  showSearch: () => void;
  showPending: () => void;
  showSavedViews: () => void;
}

export const CollectionsPaneContext = createContext<CollectionsPaneState | null>(null);

/** Standalone feature renders keep their existing inline sidebar. */
export function useCollectionsPane() {
  return useContext(CollectionsPaneContext);
}
