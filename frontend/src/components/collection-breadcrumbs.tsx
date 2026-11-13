import { useState, type ReactNode } from "react";
import { ChevronRight, Folder, MoreHorizontal } from "lucide-react";

import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { CollectionNodeRead } from "@/types";

export function CollectionBreadcrumbs({
  items,
  onNavigate,
  children,
}: {
  items: readonly CollectionNodeRead[];
  onNavigate: (path: string | null, id?: number | null) => void;
  children?: ReactNode;
}) {
  useUiLocale();
  const [open, setOpen] = useState(false);
  const current = items.at(-1);
  const ancestors = items.slice(0, -1);
  const parent = ancestors.at(-1);
  return (
    <nav
      aria-label={uiText("Breadcrumbs")}
      className="@container/trail flex min-w-0 flex-1 items-center gap-2 text-sm"
    >
      <button
        type="button"
        onClick={() => onNavigate(null)}
        aria-label={uiText("All Models")}
        title={uiText("All Models")}
        aria-current={!current ? "page" : undefined}
        className={`shrink-0 py-1 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${current ? "text-muted-foreground hover:text-foreground" : "font-medium text-foreground"}`}
      >
        <Folder className="h-4 w-4 @sm/trail:hidden" aria-hidden />
        <span className="hidden @sm/trail:inline">{uiText("All Models")}</span>
      </button>
      {ancestors.length > 0 && (
        <span
          className={`flex shrink-0 items-center gap-2 ${ancestors.length === 1 ? "@2xl/trail:hidden" : ""}`}
        >
          <ChevronRight className="h-3 w-3 text-muted-foreground" aria-hidden />
          <DropdownMenu
            open={open}
            onOpenChange={setOpen}
            align="start"
            portal
            aria-label={uiText("library.breadcrumb.ancestors")}
            trigger={
              <Button
                variant="ghost"
                size="icon-sm"
                className="h-7 w-7 text-muted-foreground"
                aria-label={uiText("library.breadcrumb.ancestors")}
                aria-haspopup="menu"
                aria-expanded={open}
                onClick={() => setOpen(!open)}
              >
                <MoreHorizontal className="h-4 w-4" aria-hidden />
              </Button>
            }
            contentClassName="w-72 max-w-[calc(100vw-2rem)] border border-border bg-popover p-1 shadow-lg"
          >
            {ancestors.map((item) => (
              <button
                type="button"
                role="menuitem"
                key={item.id}
                title={item.display_path}
                onClick={() => {
                  setOpen(false);
                  onNavigate(item.path, item.id);
                }}
                className="flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
              >
                <Folder className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <span className="min-w-0">
                  <span className="block break-words">{item.name}</span>
                  {item.display_path !== item.name && (
                    <span className="block break-words text-xs text-muted-foreground">
                      {item.display_path}
                    </span>
                  )}
                </span>
              </button>
            ))}
          </DropdownMenu>
        </span>
      )}
      {parent && (
        <span className="hidden min-w-0 items-center gap-2 @2xl/trail:flex">
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          <button
            type="button"
            title={parent.display_path}
            onClick={() => onNavigate(parent.path, parent.id)}
            className="max-w-48 truncate py-1 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {parent.name}
          </button>
        </span>
      )}
      {current && (
        <span className="flex min-w-16 items-center gap-2">
          <ChevronRight className="h-3 w-3 shrink-0 text-muted-foreground" aria-hidden />
          <span
            aria-current="page"
            title={current.display_path}
            className="max-w-sm truncate font-medium text-foreground"
          >
            {current.name}
          </span>
        </span>
      )}
      {children}
    </nav>
  );
}
