import type { EditingBase } from "@/types/editing";
import type { MultipartModelListItem, TagRead } from "@/types";
import type { TagsReview } from "@/components/entity-tags-editor";
import { EntityTagsDialog } from "@/components/entity-tags-dialog";
import { useMultipartPublication } from "@/features/library/multipart";
import { getMultipartModel, replaceMultipartModelTags } from "@/lib/api";
import { useI18n } from "@/lib/i18n";
import { toast } from "@/lib/toast";

/** The edit intent outlives a virtual card's row, retaining its captured editing base. */
export function MultipartModelTagsDialog({
  item,
  availableTags,
  onDataChange,
  canEdit: permitted = true,
  initialOpen = false,
  triggerMode = "icon",
}: {
  item: MultipartModelListItem;
  availableTags: TagRead[];
  onDataChange?: () => void;
  canEdit?: boolean;
  initialOpen?: boolean;
  triggerMode?: "icon" | "none";
}) {
  const { t } = useI18n();
  const { publish, isCurrent, active } = useMultipartPublication(item.id);
  const canEdit =
    active && permitted && (item.effective_role === "edit" || item.effective_role === "admin");

  async function saveTags(nextTags: string[], signal: AbortSignal, version: EditingBase = item) {
    if (!isCurrent() || signal.aborted) throw new DOMException("Editor retired", "AbortError");
    const saved = await replaceMultipartModelTags(item.id, nextTags, version);
    if (!(await publish(saved, signal)) || !isCurrent()) return;
    onDataChange?.();
    toast.success(t("multipart.tagsSaved"));
  }

  async function reviewTags(signal: AbortSignal): Promise<TagsReview> {
    const latest = await getMultipartModel(item.id, { signal });
    return {
      tags: latest.tags,
      canEdit: latest.effective_role === "edit" || latest.effective_role === "admin",
      save: (tags, commandSignal) => saveTags(tags, commandSignal, latest),
      adopt: async (commandSignal) => {
        if (await publish(latest, commandSignal)) onDataChange?.();
      },
    };
  }

  return (
    <EntityTagsDialog
      entityLabel={item.name}
      tags={item.tags}
      availableTags={availableTags}
      canEdit={canEdit}
      help={t("multipart.tagsHelp")}
      onSave={saveTags}
      reviewLatest={reviewTags}
      initialOpen={initialOpen}
      triggerMode={triggerMode}
      triggerClassName="bg-card/90 text-muted-foreground shadow-sm hover:bg-card hover:text-primary"
    />
  );
}
