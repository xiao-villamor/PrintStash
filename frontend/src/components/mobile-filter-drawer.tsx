"use client";

import { useEffect } from "react";
import { useMediaQuery } from "@/lib/use-media-query";
import { useOutlinerRestoration } from "@/lib/use-outliner-restoration";
import { useOutlinerCollections } from "@/lib/queries";
import { useLibraryStartup } from "@/lib/library-startup-context";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { X } from "lucide-react";
import { PrinterRead, TagRead } from "@/types";
import { FilterSidebarContent, type LibraryViewMode } from "@/components/filter-sidebar";
import { Drawer } from "@/components/ui/drawer";

interface MobileFilterDrawerProps {
  outlinerFilters: import("@/types/outliner").OutlinerFilters;
  open: boolean;
  onClose: () => void;
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
  onCreateCollection: () => void;
  canViewPrinters?: boolean;
  loading?: boolean;
  structuredFilters?: React.ReactNode;
  libraryView: LibraryViewMode;
  onLibraryViewChange: (view: LibraryViewMode) => void;
}

export function MobileFilterDrawer({ open, onClose, ...filterProps }: MobileFilterDrawerProps) {
  useUiLocale();
  const mobile = useMediaQuery("(max-width: 767px)");
  const params = { ...filterProps.outlinerFilters, view: filterProps.libraryView };
  const { restoring } = useOutlinerRestoration(params, filterProps.selectedCollection, mobile);
  const roots = useOutlinerCollections(params, mobile && !restoring);
  const { settle } = useLibraryStartup();
  useEffect(() => {
    if (!mobile || open) return;
    // Fetch alongside the cards, but only the visible tree can report ready.
    // Closing the drawer must not indefinitely starve secondary controls.
    settle("tree", restoring || roots.isPending ? "pending" : "idle");
  }, [mobile, open, restoring, roots.isPending, settle]);
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="left"
      ariaLabel={uiText("Filters")}
      containerClassName="md:hidden"
      className="w-[280px] max-w-[85vw] bg-background shadow-xl"
    >
      <div className="flex items-center justify-between p-4 border-b border-border">
        <h3 className="text-[18px] font-semibold text-foreground">{uiText("Filters")}</h3>
        <button
          onClick={onClose}
          className="text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      <div className="overflow-y-auto" style={{ height: "calc(100dvh - 60px)" }}>
        <FilterSidebarContent {...filterProps} readinessEnabled={open} />
      </div>
    </Drawer>
  );
}
