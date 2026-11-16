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
  selectedCollectionId?: number | null;
  selectedTags: string[];
  selectedPrinterId: number | null;
  selectedPrinterPresence: "any" | "none" | null;
  onCollectionChange: (path: string | null, id?: number | null) => void;
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
      ariaLabel={uiText("Collections")}
      containerClassName="md:hidden"
      className="bottom-[calc(64px+env(safe-area-inset-bottom))] flex w-64 max-w-[calc(100vw-32px)] flex-col border-r border-border bg-sidebar"
    >
      <div className="flex h-12 shrink-0 items-center justify-between pl-4 pr-2">
        <h3 className="font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
          {uiText("Collections")}
        </h3>
        <button
          onClick={onClose}
          aria-label={uiText("Close")}
          className="flex h-7 w-7 items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <X className="h-5 w-5" />
        </button>
      </div>
      <div id="mobile-collections-pane" className="min-h-0 flex-1 overflow-y-auto">
        <FilterSidebarContent
          {...filterProps}
          collectionsOnly
          filtersOpen={false}
          readinessEnabled={open}
        />
      </div>
    </Drawer>
  );
}
