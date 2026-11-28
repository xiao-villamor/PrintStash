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
import { Input, inputClasses } from "@/components/ui/input";
import { FormField } from "@/components/ui/form-field";
import { FieldErrors } from "@/components/ui/field-errors";
import { useFormValidation } from "@/lib/form-validation";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import type {
  CollectionNodeRead,
  ModelListItem,
  MultipartPartsWrite,
  MultipartModelCreate,
} from "@/types";
import { MultipartCreationBrowser } from "@/components/multipart-creation-browser";
import { MultipartPartsSummary } from "@/components/multipart-parts-summary";
import { CollectionPicker } from "@/components/collection-picker";
import {
  MULTIPART_INITIAL_LIMIT,
  multipartError,
  detailHref,
} from "@/lib/multipart-model-presentation";

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
  initialModels,
}: {
  open: boolean;
  onClose: () => void;
  collection: CollectionChoice | null;
  returnTo?: string;
  /** Models to start with, e.g. the ones selected in the library. */
  initialModels?: ModelListItem[];
}) {
  useUiLocale();
  const { t } = useI18n();
  const router = useRouter();
  const client = useQueryClient();
  const [name, setName] = useState("");
  const validation = useFormValidation();
  const [description, setDescription] = useState("");
  const [target, setTarget] = useState<CollectionChoice | null>(collection);
  const [selected, setSelected] = useState<Map<number, ModelListItem>>(
    () => new Map((initialModels ?? []).map((model) => [model.id, model])),
  );
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
      <form
        {...validation.formProps}
        onSubmit={(event) => validation.handle(event, submit)}
        className="flex min-h-0 flex-1 flex-col gap-4"
      >
        <FieldErrors errors={validation.errors}>
          <p className="text-sm text-muted-foreground">{t("multipart.pickPartsHelp")}</p>
          <div className="grid shrink-0 gap-3 sm:grid-cols-[1fr_1.4fr_16rem]">
            <FormField label={t("multipart.name")} htmlFor="multipart-name">
              <Input
                id="multipart-name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder={t("multipart.namePlaceholder")}
                maxLength={200}
                required
              />
            </FormField>
            <FormField label={t("multipart.descriptionLabel")} htmlFor="multipart-description">
              <textarea
                id="multipart-description"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                placeholder={t("multipart.descriptionPlaceholder")}
                rows={1}
                className={`${inputClasses} h-auto min-h-9 max-h-32 resize-none [field-sizing:content]`}
              />
            </FormField>
            <div className="min-w-0">
              <span className="mb-1.5 block text-field font-medium text-foreground">
                {t("multipart.collectionLabel")}
              </span>
              <DropdownMenu
                open={pickerOpen}
                onOpenChange={setPickerOpen}
                align="start"
                role="dialog"
                aria-label={t("multipart.collectionLabel")}
                portal
                className="w-full"
                contentClassName="w-96 max-w-[calc(100vw-3rem)] border border-border bg-popover p-3 text-popover-foreground shadow-lg"
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
          </div>
          <MultipartPartsSummary
            models={[...selected.values()]}
            limit={MULTIPART_INITIAL_LIMIT}
            disabled={busy}
            onRemove={(modelId) => {
              const next = new Map(selected);
              next.delete(modelId);
              setSelected(next);
            }}
          />
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
          <div className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-t border-border pt-3">
            <p role="status" className="text-sm text-muted-foreground">
              {t("multipart.selectedCount", { count: String(selected.size) })}
            </p>
            <div className="flex w-full justify-end gap-2 sm:w-auto">
              <Button type="button" variant="outline" onClick={closeModal} disabled={busy}>
                {t("multipart.cancel")}
              </Button>
              <Button type="submit" loading={busy} disabled={selectingFolder}>
                {t("multipart.create")}
              </Button>
            </div>
          </div>
        </FieldErrors>
      </form>
    </Modal>
  );
}
