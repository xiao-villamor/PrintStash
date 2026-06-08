"use client";

import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import { CollectionRead, PrinterRead, TagRead } from "@/types";
import { FilterSidebarContent } from "@/components/filter-sidebar";

interface MobileFilterDrawerProps {
  open: boolean;
  onClose: () => void;
  collections: CollectionRead[];
  tags: TagRead[];
  printers: PrinterRead[];
  selectedCollection: string | null;
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onCollectionChange: (path: string | null) => void;
  onTagsChange: (tags: string[]) => void;
  onPrinterChange: (printerId: number | null) => void;
  onPrinterPresenceChange: (presence: "any" | "none" | null) => void;
  loading?: boolean;
}

export function MobileFilterDrawer({
  open,
  onClose,
  ...filterProps
}: MobileFilterDrawerProps) {
  const drawerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  if (!open) return null;

  return (
    <div className="md:hidden fixed inset-0 z-50">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm transition-opacity"
        onClick={onClose}
      />
      <div
        ref={drawerRef}
        className="absolute left-0 top-0 bottom-0 w-[280px] max-w-[85vw] bg-[var(--surface-container-lowest)] shadow-xl slide-in-left"
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--outline-variant)]">
          <h3 className="font-headline-sm text-[18px] font-semibold text-[var(--on-surface)]">
            Filters
          </h3>
          <button
            onClick={onClose}
            className="text-[var(--on-surface-variant)] hover:text-[var(--on-surface)] p-1 rounded-full hover:bg-[var(--surface-container-low)] transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-y-auto" style={{ height: "calc(100dvh - 60px)" }}>
          <FilterSidebarContent {...filterProps} />
        </div>
      </div>
    </div>
  );
}
