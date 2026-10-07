import { captureEditingBase, requireEditingBase, requireEditingReceipt } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { batchMoveModels, batchTagModels, updateModel } from "@/lib/api/models";
import { ApiError } from "@/lib/errors";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type { ModelEditBatchResult, ModelListItem } from "@/types";

type SelectedModel = Pick<
  ModelListItem,
  "id" | "edit_epoch" | "edit_version" | "collection" | "tags"
>;
export interface LibraryEditReceipt {
  result: ModelEditBatchResult;
  /** Bound to this acknowledgment and session; never reads a newer version to authorize undo. */
  undo: () => Promise<ModelEditBatchResult>;
}

const BATCH_LIMIT = 500;
const emptyResult = (): ModelEditBatchResult => ({
  succeeded_ids: [],
  succeeded_versions: {},
  succeeded_count: 0,
  failed: [],
  failed_count: 0,
});
const versions = (models: SelectedModel[]) =>
  Object.fromEntries(models.map((model) => [model.id, captureEditingBase(model)]));
function acknowledge(result: ModelEditBatchResult, id: number): EditingBase {
  const version = result.succeeded_versions[id];
  requireEditingBase(version);
  return version;
}
function snapshot(models: SelectedModel[]): SelectedModel[] {
  return [
    ...new Map(models.map((model) => [model.id, { ...model, tags: [...model.tags] }])).values(),
  ];
}
async function chunks(
  models: SelectedModel[],
  session: number,
  operation: (models: SelectedModel[]) => Promise<ModelEditBatchResult>,
): Promise<ModelEditBatchResult> {
  const result = emptyResult();
  requireSessionVersion(session);
  for (let offset = 0; offset < models.length; offset += BATCH_LIMIT) {
    requireSessionVersion(session);
    const part = await operation(models.slice(offset, offset + BATCH_LIMIT));
    requireSessionVersion(session);
    for (const id of part.succeeded_ids) {
      const source = models.find((model) => model.id === id);
      if (!source) throw new Error("Unexpected batch acknowledgement");
      const acknowledged = acknowledge(part, id);
      requireEditingReceipt(acknowledged, source);
      result.succeeded_versions[id] = acknowledged;
    }
    result.succeeded_ids.push(...part.succeeded_ids);
    result.failed.push(...part.failed);
  }
  result.succeeded_count = result.succeeded_ids.length;
  result.failed_count = result.failed.length;
  return result;
}

export async function moveLibraryModels(
  models: SelectedModel[],
  collection: string,
): Promise<LibraryEditReceipt> {
  const session = getSessionVersion();
  const selected = snapshot(models);
  const result = await chunks(selected, session, (part) =>
    batchMoveModels(
      part.map((model) => model.id),
      collection,
      versions(part),
    ),
  );
  return {
    result,
    async undo() {
      requireSessionVersion(session);
      const groups = new Map<string, SelectedModel[]>();
      const successful = new Set(result.succeeded_ids);
      for (const model of selected) {
        if (!successful.has(model.id)) continue;
        const collection = model.collection ?? "";
        const group = groups.get(collection) ?? [];
        group.push({ ...model, ...acknowledge(result, model.id) });
        groups.set(collection, group);
      }
      const undone = emptyResult();
      for (const [collection, models] of groups) {
        const part = await chunks(models, session, (part) =>
          batchMoveModels(
            part.map((model) => model.id),
            collection,
            versions(part),
          ),
        );
        undone.succeeded_ids.push(...part.succeeded_ids);
        Object.assign(undone.succeeded_versions, part.succeeded_versions);
        undone.failed.push(...part.failed);
      }
      undone.succeeded_count = undone.succeeded_ids.length;
      undone.failed_count = undone.failed.length;
      return undone;
    },
  };
}

export async function tagLibraryModels(
  models: SelectedModel[],
  add: string[],
  remove: string[],
): Promise<LibraryEditReceipt> {
  const session = getSessionVersion();
  const selected = snapshot(models);
  const result = await chunks(selected, session, (part) =>
    batchTagModels(
      part.map((model) => model.id),
      add,
      remove,
      versions(part),
    ),
  );
  return {
    result,
    async undo() {
      requireSessionVersion(session);
      const undone = emptyResult();
      const successful = new Set(result.succeeded_ids);
      for (const model of selected) {
        if (!successful.has(model.id)) continue;
        requireSessionVersion(session);
        try {
          const restored = await updateModel(
            model.id,
            { tags: model.tags },
            acknowledge(result, model.id),
          );
          requireSessionVersion(session);
          undone.succeeded_ids.push(model.id);
          undone.succeeded_versions[model.id] = captureEditingBase(restored);
        } catch (error) {
          requireSessionVersion(session);
          if (!(error instanceof ApiError) || error.status !== 412) throw error;
          undone.failed.push({ model_id: model.id, reason: "edit_conflict" });
        }
      }
      undone.succeeded_count = undone.succeeded_ids.length;
      undone.failed_count = undone.failed.length;
      return undone;
    },
  };
}
