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
export type LibraryBatchCompletion =
  | { status: "complete" }
  | { status: "interrupted"; error: unknown; unconfirmedIds: number[]; unattemptedIds: number[] };
export interface LibraryBatchOutcome {
  result: ModelEditBatchResult;
  completion: LibraryBatchCompletion;
}
export interface LibraryEditReceipt extends LibraryBatchOutcome {
  /** Bound to this acknowledgment and session; never reads a newer version to authorize undo. */
  undo: () => Promise<LibraryBatchOutcome>;
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
function append(result: ModelEditBatchResult, part: ModelEditBatchResult): void {
  result.succeeded_ids.push(...part.succeeded_ids);
  Object.assign(result.succeeded_versions, part.succeeded_versions);
  result.failed.push(...part.failed);
  result.succeeded_count = result.succeeded_ids.length;
  result.failed_count = result.failed.length;
}

/** An incomplete/malformed receipt cannot confirm any row of that request. */
function validateReceipt(part: ModelEditBatchResult, selected: SelectedModel[]): void {
  const remaining = new Map(selected.map((model) => [model.id, model]));
  if (
    part.succeeded_count !== part.succeeded_ids.length ||
    part.failed_count !== part.failed.length ||
    Object.keys(part.succeeded_versions).length !== part.succeeded_ids.length
  )
    throw new Error("Invalid batch acknowledgement");
  for (const id of part.succeeded_ids) {
    const source = remaining.get(id);
    if (!source) throw new Error("Unexpected batch acknowledgement");
    requireEditingReceipt(acknowledge(part, id), source);
    remaining.delete(id);
  }
  for (const failure of part.failed) {
    if (!remaining.delete(failure.model_id)) throw new Error("Unexpected batch acknowledgement");
  }
  if (remaining.size) throw new Error("Incomplete batch acknowledgement");
}

async function chunks(
  models: SelectedModel[],
  session: number,
  operation: (models: SelectedModel[]) => Promise<ModelEditBatchResult>,
  size = BATCH_LIMIT,
): Promise<LibraryBatchOutcome> {
  const result = emptyResult();
  requireSessionVersion(session);
  for (let offset = 0; offset < models.length; offset += size) {
    requireSessionVersion(session);
    const selected = models.slice(offset, offset + size);
    try {
      const part = await operation(selected);
      requireSessionVersion(session);
      validateReceipt(part, selected);
      append(result, part);
    } catch (error) {
      requireSessionVersion(session);
      return {
        result,
        completion: {
          status: "interrupted",
          error,
          unconfirmedIds: selected.map((model) => model.id),
          unattemptedIds: models.slice(offset + size).map((model) => model.id),
        },
      };
    }
  }
  return { result, completion: { status: "complete" } };
}

export async function moveLibraryModels(
  models: SelectedModel[],
  collection: string,
): Promise<LibraryEditReceipt> {
  const session = getSessionVersion();
  const selected = snapshot(models);
  const outcome = await chunks(selected, session, (part) =>
    batchMoveModels(
      part.map((model) => model.id),
      collection,
      versions(part),
    ),
  );
  return {
    ...outcome,
    async undo() {
      requireSessionVersion(session);
      const groups = new Map<string, SelectedModel[]>();
      const successful = new Set(outcome.result.succeeded_ids);
      for (const model of selected) {
        if (!successful.has(model.id)) continue;
        const collection = model.collection ?? "";
        const group = groups.get(collection) ?? [];
        group.push({ ...model, ...acknowledge(outcome.result, model.id) });
        groups.set(collection, group);
      }
      const undone = emptyResult();
      const pending = new Set([...groups.values()].flat().map((model) => model.id));
      for (const [collection, models] of groups) {
        models.forEach((model) => pending.delete(model.id));
        const part = await chunks(models, session, (part) =>
          batchMoveModels(
            part.map((model) => model.id),
            collection,
            versions(part),
          ),
        );
        append(undone, part.result);
        if (part.completion.status === "interrupted")
          return {
            result: undone,
            completion: {
              ...part.completion,
              unattemptedIds: [...part.completion.unattemptedIds, ...pending],
            },
          };
      }
      return { result: undone, completion: { status: "complete" } };
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
  const outcome = await chunks(selected, session, (part) =>
    batchTagModels(
      part.map((model) => model.id),
      add,
      remove,
      versions(part),
    ),
  );
  return {
    ...outcome,
    async undo() {
      requireSessionVersion(session);
      const successful = new Set(outcome.result.succeeded_ids);
      const acknowledged = selected
        .filter((model) => successful.has(model.id))
        .map((model) => ({
          ...model,
          ...acknowledge(outcome.result, model.id),
        }));
      return chunks(
        acknowledged,
        session,
        async ([model]) => {
          try {
            const restored = await updateModel(model.id, { tags: model.tags }, model);
            return {
              succeeded_ids: [model.id],
              succeeded_count: 1,
              succeeded_versions: { [model.id]: captureEditingBase(restored) },
              failed: [],
              failed_count: 0,
            };
          } catch (error) {
            requireSessionVersion(session);
            if (!(error instanceof ApiError) || error.status !== 412) throw error;
            return {
              ...emptyResult(),
              failed: [{ model_id: model.id, reason: "edit_conflict" }],
              failed_count: 1,
            };
          }
        },
        1,
      );
    },
  };
}
