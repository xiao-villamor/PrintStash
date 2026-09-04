"use client";

import { useCallback, useState, type ReactNode } from "react";

import { MobileFilterContext } from "@/lib/mobile-filter-context";

export function MobileFilterProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const openDrawer = useCallback(() => setOpen(true), []);
  const closeDrawer = useCallback(() => setOpen(false), []);

  return (
    <MobileFilterContext.Provider value={{ open, openDrawer, closeDrawer }}>
      {children}
    </MobileFilterContext.Provider>
  );
}
