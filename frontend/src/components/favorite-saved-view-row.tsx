import { useState } from "react";
import type { SavedViewRead } from "@/types";
import { uiText } from "@/lib/locale";
import { useI18n } from "@/lib/i18n";
import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { TitleEditor } from "@/components/title-editor";

/** A personal saved view; command authority stays with the Library owner. */
export function FavoriteSavedViewRow({
  view,
  onOpen,
  onRename,
  onUpdate,
  onDuplicate,
  onDelete,
}: {
  view: SavedViewRead;
  onOpen: () => void;
  onRename: (name: string) => Promise<void>;
  onUpdate: () => Promise<void>;
  onDuplicate: () => Promise<void>;
  onDelete: () => Promise<void>;
}) {
  const { t } = useI18n();
  const [rename, setRename] = useState(false);
  const [name, setName] = useState(view.name);
  const [more, setMore] = useState(false);
  const [remove, setRemove] = useState(false);
  const [busy, setBusy] = useState(false);
  async function run(command: () => Promise<void>, done: () => void) {
    if (busy) return;
    setBusy(true);
    try {
      await command();
      done();
    } catch {
      /* The command owner reports the failure. */
    } finally {
      setBusy(false);
    }
  }
  const filters = view.filters;
  const summary = [
    filters.q || uiText("All Models"),
    ...(filters.material_type ?? []),
    ...(filters.tag ?? []),
    ...(filters.revision_status ?? []),
    filters.sort ? t(`aiSearch.sort.${filters.sort}`) : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4 last:border-0">
      <div className="min-w-0 flex-1 basis-64">
        <h2 className="text-sm font-semibold">{view.name}</h2>
        <p className="mt-1 text-xs text-muted-foreground">{summary}</p>
      </div>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="xs" onClick={onOpen}>
          {uiText("Open")}
        </Button>
        <TitleEditor
          open={rename}
          onOpenChange={setRename}
          value={name}
          onValueChange={setName}
          busy={busy}
          label={uiText("Rename {value1}", { value1: view.name })}
          fieldLabel={uiText("View name")}
          requiredMessage={uiText("View name")}
          maxLength={128}
          onSave={() =>
            void run(
              () => onRename(name.trim()),
              () => setRename(false),
            )
          }
          trigger={
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                setName(view.name);
                setRename(true);
              }}
            >
              {uiText("Rename")}
            </Button>
          }
        />
        <DropdownMenu
          open={more}
          onOpenChange={setMore}
          portal
          align="end"
          trigger={
            <Button
              variant="outline"
              size="xs"
              onClick={() => setMore(!more)}
              aria-haspopup="menu"
              aria-expanded={more}
            >
              {t("nav.more")}
            </Button>
          }
          contentClassName="w-56 border border-border bg-popover p-1 text-popover-foreground shadow-lg"
        >
          <Button
            variant="ghost"
            size="xs"
            role="menuitem"
            className="w-full justify-start"
            disabled={busy}
            onClick={() => void run(onUpdate, () => setMore(false))}
          >
            {uiText("Update with current filters")}
          </Button>
          <Button
            variant="ghost"
            size="xs"
            role="menuitem"
            className="w-full justify-start"
            disabled={busy}
            onClick={() => void run(onDuplicate, () => setMore(false))}
          >
            {uiText("Duplicate")}
          </Button>
          <Button
            variant="ghost"
            size="xs"
            role="menuitem"
            className="w-full justify-start text-destructive"
            disabled={busy}
            onClick={() => {
              setMore(false);
              setRemove(true);
            }}
          >
            {uiText("Delete")}
          </Button>
        </DropdownMenu>
      </div>
      <ConfirmModal
        open={remove}
        onClose={() => {
          if (!busy) setRemove(false);
        }}
        title={uiText("Delete saved view?")}
        description={uiText("“{value1}” will be removed. Models are not affected.", {
          value1: view.name,
        })}
        busy={busy}
        onConfirm={() => void run(onDelete, () => setRemove(false))}
      />
    </div>
  );
}
