"use client";

import { useMemo, useState } from "react";
import { Boxes, Plus, Trash2 } from "lucide-react";

import { replacePartOptions } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";
import type { FileRead, ModelRead, PartGroupRead, PartGroupWrite } from "@/types";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

type DraftOption = {
  key: string;
  fileId: number | null;
  name: string;
  isDefault: boolean;
};

type DraftGroup = {
  key: string;
  name: string;
  options: DraftOption[];
};

let draftSequence = 0;
const draftKey = (prefix: string) => `draft-${prefix}-${++draftSequence}`;

function toDraft(groups: PartGroupRead[]): DraftGroup[] {
  return groups.map((group) => ({
    key: `group-${group.id}`,
    name: group.name,
    options: group.options.map((option) => ({
      key: `option-${option.id}`,
      fileId: option.file_id,
      name: option.name,
      isDefault: option.is_default,
    })),
  }));
}

function usedFileIds(groups: DraftGroup[], exceptKey?: string): Set<number> {
  return new Set(
    groups.flatMap((group) =>
      group.options.flatMap((option) =>
        option.key !== exceptKey && option.fileId !== null ? [option.fileId] : [],
      ),
    ),
  );
}

function validDraft(groups: DraftGroup[]): boolean {
  const groupNames = new Set<string>();
  const files = new Set<number>();
  for (const group of groups) {
    const groupName = group.name.trim().toLocaleLowerCase();
    if (!groupName || groupNames.has(groupName) || group.options.length < 2) return false;
    groupNames.add(groupName);
    const optionNames = new Set<string>();
    if (group.options.filter((option) => option.isDefault).length !== 1) return false;
    for (const option of group.options) {
      const optionName = option.name.trim().toLocaleLowerCase();
      if (
        option.fileId === null ||
        !optionName ||
        optionNames.has(optionName) ||
        files.has(option.fileId)
      ) {
        return false;
      }
      optionNames.add(optionName);
      files.add(option.fileId);
    }
  }
  return true;
}

export function PartOptionsDialog({
  modelId,
  files,
  groups,
  canEdit,
  onModel,
}: {
  modelId: number;
  files: FileRead[];
  groups: PartGroupRead[];
  canEdit: boolean;
  onModel: (model: ModelRead) => void;
}) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<DraftGroup[]>([]);
  const [saving, setSaving] = useState(false);
  const filesById = useMemo(() => new Map(files.map((file) => [file.id, file])), [files]);

  function updateGroup(groupKey: string, update: (group: DraftGroup) => DraftGroup) {
    setDraft((current) => current.map((group) => (group.key === groupKey ? update(group) : group)));
  }

  function addGroup() {
    setDraft((current) => {
      const used = usedFileIds(current);
      const available = files.filter((file) => !used.has(file.id)).slice(0, 2);
      if (available.length < 2) return current;
      return [
        ...current,
        {
          key: draftKey("group"),
          name: "",
          options: available.map((file, index) => ({
            key: draftKey("option"),
            fileId: file.id,
            name: file.original_filename.replace(/\.[^.]+$/, ""),
            isDefault: index === 0,
          })),
        },
      ];
    });
  }

  function addOption(groupKey: string) {
    setDraft((current) => {
      const used = usedFileIds(current);
      const file = files.find((candidate) => !used.has(candidate.id));
      if (!file) return current;
      return current.map((group) =>
        group.key === groupKey
          ? {
              ...group,
              options: [
                ...group.options,
                {
                  key: draftKey("option"),
                  fileId: file.id,
                  name: file.original_filename.replace(/\.[^.]+$/, ""),
                  isDefault: false,
                },
              ],
            }
          : group,
      );
    });
  }

  async function save() {
    if (!validDraft(draft)) {
      toast.error(t("parts.invalid"));
      return;
    }
    const payload: PartGroupWrite[] = draft.map((group) => ({
      name: group.name.trim(),
      options: group.options.map((option) => {
        if (option.fileId === null) throw new Error("validated Part Option has no file");
        return {
          file_id: option.fileId,
          name: option.name.trim(),
          is_default: option.isDefault,
        };
      }),
    }));
    setSaving(true);
    try {
      onModel(await replacePartOptions(modelId, payload));
      toast.success(t("parts.saved"));
      setOpen(false);
    } catch (error) {
      toast.error(error);
    } finally {
      setSaving(false);
    }
  }

  const availableForNewGroup = files.length - usedFileIds(draft).size >= 2;

  return (
    <section className="mb-5 rounded-lg border border-outline-variant bg-surface-container-low p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-2xl">
          <h3 className="flex items-center gap-2 text-sm font-semibold text-on-surface">
            <Boxes className="h-4 w-4 text-primary" aria-hidden />
            {t("parts.title")}
          </h3>
          <p className="mt-1 text-sm leading-relaxed text-on-surface-variant">
            {t("parts.description")}
          </p>
        </div>
        {canEdit && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={files.length < 2}
            onClick={() => {
              setDraft(toDraft(groups));
              setOpen(true);
            }}
          >
            {t("parts.manage")}
          </Button>
        )}
      </div>

      {groups.length === 0 ? (
        <p className="mt-3 font-mono text-xs text-on-surface-variant">
          {files.length < 2 ? t("parts.needFiles") : t("parts.empty")}
        </p>
      ) : (
        <div className="mt-3 grid gap-2 sm:grid-cols-2">
          {groups.map((group) => (
            <div key={group.id} className="rounded border border-outline-variant bg-surface p-3">
              <p className="text-sm font-medium text-on-surface">{group.name}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                {group.options.map((option) => (
                  <span
                    key={option.id}
                    className="inline-flex max-w-full items-center gap-1 rounded-full border border-outline-variant bg-surface-container-low px-2 py-1 text-xs text-on-surface-variant"
                    title={filesById.get(option.file_id)?.original_filename}
                  >
                    <span className="truncate">{option.name}</span>
                    {option.is_default && (
                      <span className="font-mono text-3xs uppercase tracking-wider text-primary">
                        {t("parts.default")}
                      </span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <Modal open={open} onClose={() => !saving && setOpen(false)} title={t("parts.dialogTitle")}>
        <div className="space-y-4">
          <p className="text-sm leading-relaxed text-on-surface-variant">
            {t("parts.description")}
          </p>
          <div className="max-h-[60vh] space-y-3 overflow-y-auto pr-1">
            {draft.map((group, groupIndex) => {
              const used = usedFileIds(draft);
              return (
                <fieldset
                  key={group.key}
                  className="space-y-3 rounded-lg border border-outline-variant bg-surface-container-low p-3"
                >
                  <div className="flex items-end gap-2">
                    <label className="min-w-0 flex-1 text-sm font-medium text-on-surface">
                      <span className="mb-1 block">{t("parts.groupName")}</span>
                      <Input
                        value={group.name}
                        maxLength={128}
                        placeholder={t("parts.groupPlaceholder")}
                        onChange={(event) =>
                          updateGroup(group.key, (current) => ({
                            ...current,
                            name: event.target.value,
                          }))
                        }
                      />
                    </label>
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      aria-label={t("parts.removeGroup")}
                      onClick={() =>
                        setDraft((current) => current.filter((item) => item.key !== group.key))
                      }
                    >
                      <Trash2 className="h-4 w-4" aria-hidden />
                    </Button>
                  </div>

                  {group.options.map((option) => (
                    <div
                      key={option.key}
                      className="grid gap-2 rounded border border-outline-variant bg-surface p-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]"
                    >
                      <label className="text-xs font-medium text-on-surface-variant">
                        <span className="mb-1 block">{t("parts.sourceFile")}</span>
                        <select
                          value={option.fileId ?? ""}
                          onChange={(event) =>
                            updateGroup(group.key, (current) => ({
                              ...current,
                              options: current.options.map((item) =>
                                item.key === option.key
                                  ? { ...item, fileId: Number(event.target.value) }
                                  : item,
                              ),
                            }))
                          }
                          className="h-10 w-full rounded-md border border-outline-variant bg-surface px-3 text-sm text-on-surface focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                        >
                          {files.map((file) => (
                            <option
                              key={file.id}
                              value={file.id}
                              disabled={used.has(file.id) && file.id !== option.fileId}
                            >
                              {file.original_filename}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label className="text-xs font-medium text-on-surface-variant">
                        <span className="mb-1 block">{t("parts.optionName")}</span>
                        <Input
                          value={option.name}
                          maxLength={128}
                          placeholder={t("parts.optionPlaceholder")}
                          onChange={(event) =>
                            updateGroup(group.key, (current) => ({
                              ...current,
                              options: current.options.map((item) =>
                                item.key === option.key
                                  ? { ...item, name: event.target.value }
                                  : item,
                              ),
                            }))
                          }
                        />
                      </label>
                      <div className="flex items-end justify-between gap-1 md:justify-end">
                        <label className="flex h-10 items-center gap-2 px-2 text-xs text-on-surface-variant">
                          <input
                            type="radio"
                            name={`part-default-${groupIndex}`}
                            checked={option.isDefault}
                            onChange={() =>
                              updateGroup(group.key, (current) => ({
                                ...current,
                                options: current.options.map((item) => ({
                                  ...item,
                                  isDefault: item.key === option.key,
                                })),
                              }))
                            }
                          />
                          {t("parts.makeDefault")}
                        </label>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={t("parts.removeOption")}
                          onClick={() =>
                            updateGroup(group.key, (current) => {
                              const remaining = current.options.filter(
                                (item) => item.key !== option.key,
                              );
                              const options = remaining.map((item, index) => ({
                                ...item,
                                isDefault: option.isDefault ? index === 0 : item.isDefault,
                              }));
                              return { ...current, options };
                            })
                          }
                        >
                          <Trash2 className="h-4 w-4" aria-hidden />
                        </Button>
                      </div>
                    </div>
                  ))}

                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    disabled={used.size >= files.length}
                    onClick={() => addOption(group.key)}
                  >
                    <Plus className="h-4 w-4" aria-hidden />
                    {t("parts.addOption")}
                  </Button>
                </fieldset>
              );
            })}
          </div>

          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!availableForNewGroup}
            onClick={addGroup}
          >
            <Plus className="h-4 w-4" aria-hidden />
            {t("parts.addGroup")}
          </Button>
          <div className="flex justify-end gap-2 border-t border-outline-variant pt-4">
            <Button
              type="button"
              variant="outline"
              disabled={saving}
              onClick={() => setOpen(false)}
            >
              {t("parts.cancel")}
            </Button>
            <Button type="button" disabled={saving} onClick={() => void save()}>
              {saving ? t("parts.saving") : t("parts.save")}
            </Button>
          </div>
        </div>
      </Modal>
    </section>
  );
}
