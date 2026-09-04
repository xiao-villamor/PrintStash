"use client";

import { createContext, useContext } from "react";

interface MobileFilterContextValue {
  open: boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
}

export const MobileFilterContext = createContext<MobileFilterContextValue | null>(null);

export function useMobileFilterDrawer() {
  const ctx = useContext(MobileFilterContext);
  if (!ctx) {
    throw new Error("useMobileFilterDrawer must be used within MobileFilterProvider");
  }
  return ctx;
}
