import { useState } from "react";
import { Check, ChevronDown } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import type { FileRead } from "@/types";
import { revisionStatusLabel } from "./presentation";

function revisionName(file: FileRead) {
  return uiText("Rev {value1}", { value1: String(file.gcode_revision_number ?? file.version) });
}

/** The revision a Print view describes, switchable without leaving the view. */
export function RevisionPicker({
  files,
  selected,
  onSelect,
}: {
  files: readonly FileRead[];
  selected: FileRead | null;
  onSelect: (id: number) => void;
}) {
  useUiLocale();
  const [open, setOpen] = useState(false);
  if (!selected) return null;
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <span className="text-sm text-muted-foreground">{uiText("Revision")}</span>
      <DropdownMenu
        open={open}
        onOpenChange={setOpen}
        portal
        align="start"
        aria-label={uiText("Inspect revision")}
        contentClassName="w-72 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        trigger={
          <Button
            type="button"
            variant="outline"
            size="xs"
            className="dropdown-field"
            data-menu-trigger
            aria-haspopup="menu"
            aria-expanded={open}
            title={selected.original_filename}
            onClick={() => setOpen((value) => !value)}
          >
            {revisionName(selected)} · {revisionStatusLabel(selected.revision_status)}
            <ChevronDown className="size-3.5 text-muted-foreground" aria-hidden />
          </Button>
        }
      >
        {files.map((file) => (
          <button
            key={file.id}
            type="button"
            role="menuitemradio"
            aria-checked={file.id === selected.id}
            title={file.original_filename}
            onClick={() => {
              setOpen(false);
              onSelect(file.id);
            }}
            className="field-option aria-checked:bg-accent aria-checked:text-accent-foreground"
          >
            <span className="min-w-0 flex-1">
              <span className="block">
                {revisionName(file)}
                {file.revision_label ? ` · ${file.revision_label}` : ""}
              </span>
              <span className="block text-xs text-muted-foreground">
                {revisionStatusLabel(file.revision_status)}
                {file.is_recommended ? ` · ${uiText("Recommended")}` : ""}
              </span>
            </span>
            {file.id === selected.id && <Check className="size-3.5 shrink-0" aria-hidden />}
          </button>
        ))}
      </DropdownMenu>
    </div>
  );
}
