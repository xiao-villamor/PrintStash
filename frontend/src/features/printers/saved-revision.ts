import { useQuery } from "@tanstack/react-query";
import { modelDetailOptions } from "@/features/library/model-detail";
import { uiText } from "@/lib/locale";
import type { FileRead } from "@/types";

export interface RevisionSelection {
  modelId: number;
  fileId: number;
}

export function useSavedRevision(selection: RevisionSelection | null, repeat: boolean) {
  const options = modelDetailOptions(selection?.modelId ?? null);
  const query = useQuery({
    ...options,
    staleTime: repeat ? 0 : options.staleTime,
  });
  const model = query.data;
  const canUseRevision =
    selection &&
    model?.id === selection.modelId &&
    query.isSuccess &&
    (!repeat ||
      (!query.isFetching && (model.effective_role === "edit" || model.effective_role === "admin")));
  const file = canUseRevision
    ? model.files.find(
        (value) =>
          value.id === selection.fileId &&
          value.model_id === model.id &&
          value.file_type === "gcode",
      )
    : undefined;
  return { query, file };
}

export function printFormat(file: FileRead) {
  return file.original_filename.toLowerCase().endsWith(".bgcode") ? "bgcode_binary" : "gcode_text";
}

export function isSlicedRevision(file: FileRead) {
  return file.original_filename.toLowerCase().endsWith(".gcode.3mf");
}

export function revisionLabel(revision: FileRead) {
  return (
    revision.revision_label ||
    `${uiText("Revision")} ${revision.gcode_revision_number ?? revision.version}`
  );
}
