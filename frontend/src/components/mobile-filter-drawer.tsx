"use client";

import { X } from "lucide-react";
import { CollectionRead, PrinterRead, TagRead } from "@/types";
import { FilterSidebarContent } from "@/components/filter-sidebar";
import { Drawer } from "@/components/ui/drawer";

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
  onCreateCollection: () => void;
  canViewPrinters?: boolean;
  loading?: boolean;
}

export function MobileFilterDrawer({
  open,
  onClose,
  ...filterProps
}: MobileFilterDrawerProps) {
  return (
    <Drawer
      open={open}
      onClose={onClose}
      side="left"
      ariaLabel="Filters"
      containerClassName="md:hidden"
      className="w-[280px] max-w-[85vw] bg-background shadow-xl"
    >
        <div className="flex items-center justify-between p-4 border-b border-border">
          <h3 className="text-[18px] font-semibold text-foreground">
            Filters
          </h3>
          <button
            onClick={onClose}
            className="text-muted-foreground hover:text-foreground p-1 rounded-full hover:bg-muted transition-colors"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="overflow-y-auto" style={{ height: "calc(100dvh - 60px)" }}>
          <FilterSidebarContent {...filterProps} />
        </div>
    </Drawer>
  );
}
