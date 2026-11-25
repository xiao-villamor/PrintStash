import { useRef, useState, type ComponentProps } from "react";
import { SlidersHorizontal } from "lucide-react";
import { LibraryFilterPicker } from "@/components/library-filter-picker";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useI18n } from "@/lib/i18n";

/** Opening the picker is local to the toolbar. Applied filters still belong to the Library. */
export function LibraryFilterControl({
  className,
  activeCount,
  onOpen,
  ...picker
}: {
  className: string;
  activeCount: number;
  onOpen: () => void;
} & Omit<ComponentProps<typeof LibraryFilterPicker>, "onClose">) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  return (
    <DropdownMenu
      open={open}
      onOpenChange={setOpen}
      animate={false}
      portal
      align="start"
      role="dialog"
      aria-label={t("Filter Models")}
      className={className}
      contentClassName="w-[352px] overflow-hidden rounded-md border border-border bg-popover text-popover-foreground shadow-lg"
      trigger={
        <Button
          ref={trigger}
          type="button"
          variant="ghost"
          size="xs"
          data-menu-trigger
          onClick={() => {
            if (!open) onOpen();
            setOpen(!open);
          }}
          aria-label={t("Filters")}
          aria-haspopup="dialog"
          aria-expanded={open}
          aria-controls="library-filter-panel"
          className="h-8 w-full gap-2 aria-expanded:bg-accent aria-expanded:text-accent-foreground"
        >
          <SlidersHorizontal className="h-3.5 w-3.5 shrink-0" aria-hidden />
          <span className="library-toolbar-filter-label">{t("Filters")}</span>
          {activeCount > 0 && (
            <span className="text-xs tabular-nums text-muted-foreground">{activeCount}</span>
          )}
        </Button>
      }
    >
      {open && (
        <LibraryFilterPicker
          {...picker}
          onClose={() => {
            setOpen(false);
            trigger.current?.focus({ preventScroll: true });
          }}
        />
      )}
    </DropdownMenu>
  );
}
