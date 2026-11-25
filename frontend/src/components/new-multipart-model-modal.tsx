import { useQueryClient } from "@tanstack/react-query";
import { refreshLibraryMetadata } from "@/features/library/metadata";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { ChevronDown, Folder } from "lucide-react";
import { useState } from "react";
import { uiText } from "@/lib/locale";
import { useI18n, useUiLocale } from "@/lib/i18n";
import { useRouter } from "@/lib/navigation";
import { createMultipartModel } from "@/lib/api";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import type {
  CollectionNodeRead,
  ModelListItem,
  MultipartPartsWrite,
  MultipartModelCreate,
} from "@/types";
import { MultipartCreationBrowser } from "@/components/multipart-creation-browser";
import { CollectionPicker } from "@/components/collection-picker";
import { multipartError, detailHref } from "@/lib/multipart-model-presentation";

/** The folder a new set starts in: its id is what is saved, its path what is shown chosen. */
export type CollectionChoice = CollectionNodeRead;

function initialParts(models: ModelListItem[]): MultipartPartsWrite["parts"] {
  const names = new Set<string>();
  return models.map((model) => {
    const base = model.name.trim().replace(/\s+/g, " ").slice(0, 120);
    let name = base;
    let suffix = 1;
    while (names.has(name.toLocaleLowerCase())) name = base + " (" + ++suffix + ")";
    names.add(name.toLocaleLowerCase());
    return { name, quantity: 1, choices: [{ model_id: model.id }] };
  });
}

export function NewMultipartModelModal({
  open,
  onClose,
  collection,
  returnTo,
}: {
  open: boolean;
  onClose: () => void;
  collection: CollectionChoice | null;
  returnTo?: string;
}) {
  useUiLocale();
  const { t } = useI18n();
  const router = useRouter();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState<CollectionChoice | null>(collection);
  const [selected, setSelected] = useState<Map<number, ModelListItem>>(new Map());
  const [pickerOpen, setPickerOpen] = useState(false);
  const [selectingFolder, setSelectingFolder] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function closeModal() {
    setSelectingFolder(false);
    setSelected(new Map());
    setName("");
    setDescription("");
    setTarget(collection);
    setPickerOpen(false);
    setError(null);
    onClose();
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || busy || selectingFolder) return;
    const session = getSessionVersion();
    setBusy(true);
    setError(null);
    try {
      const payload: MultipartModelCreate = {
        name: name.trim(),
        description: description.trim() || null,
        collection_id: target?.id ?? null,
      };
      if (selected.size) payload.parts = initialParts([...selected.values()]);
      const created = await createMultipartModel(payload);
      requireSessionVersion(session);
      refreshLibraryMetadata(client, session);
      closeModal();
      router.push(detailHref(created.id, returnTo));
    } catch (cause) {
      setError(multipartError(cause, t, "multipart.createError"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={busy ? () => undefined : closeModal}
      title={t("multipart.new")}
      className="flex max-h-[92dvh] max-w-5xl flex-col"
    >
      <form onSubmit={submit} className="flex min-h-0 flex-1 flex-col gap-4">
        <p className="text-sm text-muted-foreground">{t("multipart.pickPartsHelp")}</p>
        <div className="grid shrink-0 gap-3 sm:grid-cols-2">
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">{t("multipart.name")}</span>
            <Input
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder={t("multipart.namePlaceholder")}
              maxLength={200}
              required
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm font-medium">{t("multipart.descriptionLabel")}</span>
            <textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              placeholder={t("multipart.descriptionPlaceholder")}
              rows={1}
              className="w-full rounded-md border border-input bg-field-surface px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
          </label>
        </div>
        <MultipartCreationBrowser
          active={open}
          onBusyChange={setSelectingFolder}
          selected={selected}
          onSelectionChange={setSelected}
          initialCollection={collection}
          disabled={busy}
        />
        {error && (
          <p
            role="alert"
            className="rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive"
          >
            {error}
          </p>
        )}
        <div className="flex shrink-0 flex-wrap items-end justify-between gap-3 border-t border-border pt-3">
          <p role="status" className="text-sm text-muted-foreground">
            {t("multipart.selectedCount", { count: String(selected.size) })}
          </p>
          <div className="min-w-0 space-y-1.5 sm:w-64">
            <span className="text-sm font-medium">{t("multipart.collectionLabel")}</span>
            <DropdownMenu
              open={pickerOpen}
              onOpenChange={setPickerOpen}
              align="start"
              role="dialog"
              aria-label={t("multipart.collectionLabel")}
              portal
              className="w-full"
              contentClassName="w-96 max-w-[calc(100vw-3rem)] rounded-md border border-border bg-popover p-3 text-popover-foreground shadow-lg"
              trigger={
                <Button
                  type="button"
                  variant="outline"
                  className="dropdown-field w-full justify-start font-normal"
                  aria-label={t("multipart.collectionLabel")}
                  data-menu-trigger
                  aria-haspopup="dialog"
                  aria-expanded={pickerOpen}
                  title={target?.display_path ?? t("multipart.vaultOnly")}
                  disabled={busy}
                  onClick={() => setPickerOpen((value) => !value)}
                >
                  <Folder className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <span className="min-w-0 flex-1 truncate text-left">
                    {target?.name ?? t("multipart.vaultOnly")}
                  </span>
                  <ChevronDown className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                </Button>
              }
            >
              <CollectionPicker
                autoFocus
                minRole="edit"
                selectedPath={target?.path ?? ""}
                onSelect={(picked) => {
                  setTarget(picked);
                  setPickerOpen(false);
                }}
                noneLabel={t("multipart.vaultOnly")}
                emptyLabel={uiText("No editable collections.")}
              />
            </DropdownMenu>
            {target && target.display_path !== target.name && (
              <p className="break-words text-xs text-muted-foreground">{target.display_path}</p>
            )}
          </div>

          <div className="flex w-full justify-end gap-2 sm:w-auto">
            <Button type="button" variant="outline" onClick={closeModal} disabled={busy}>
              {t("multipart.cancel")}
            </Button>
            <Button type="submit" loading={busy} disabled={!name.trim() || selectingFolder}>
              {t("multipart.create")}
            </Button>
          </div>
        </div>
      </form>
    </Modal>
  );
}
