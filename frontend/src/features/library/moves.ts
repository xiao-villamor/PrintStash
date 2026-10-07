import { useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { getModel, updateModel } from "@/lib/api/models";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { ModelDrag } from "@/lib/model-dnd";
import type { ModelRead } from "@/types";
import type { LibraryEntry } from "./navigation-state";

type ReviewState =
  | { phase: "needs-review" | "reviewing" | "review-failed" | "denied" | "saving" }
  | { phase: "reviewed"; model: ModelRead };
export type MoveIssue = Readonly<{
  source: ModelDrag;
  destination: string | null;
  problem: "conflict" | "unconfirmed";
  review: ReviewState;
}>;
type MoveRecord = {
  source: ModelDrag;
  destination: string | null;
  issue: MoveIssue | null;
  controller: AbortController | null;
};

/** Each Model has one outstanding intent; unrelated Model moves can proceed independently. */
export function useModelMoves(entry: LibraryEntry, onConfirmed: () => Promise<void>) {
  const client = useQueryClient();
  const session = useSyncExternalStore(onAuthChange, getSessionVersion);
  const scope = useMemo(
    () => ({ entry, session, records: new Map<number, MoveRecord>() }),
    [entry, session],
  );
  const [presentation, setPresentation] = useState<{
    scope: typeof scope;
    issues: MoveIssue[];
  } | null>(null);
  const activeScope = useRef<typeof scope | null>(null);
  useLayoutEffect(() => {
    activeScope.current = scope;
    return () => {
      activeScope.current = null;
      for (const record of scope.records.values()) record.controller?.abort();
      scope.records.clear();
    };
  }, [scope]);
  const current = () =>
    activeScope.current === scope &&
    entry.session === getSessionVersion() &&
    session === getSessionVersion();
  const owns = (record: MoveRecord) => current() && scope.records.get(record.source.id) === record;
  function present() {
    if (current())
      setPresentation({
        scope,
        issues: [...scope.records.values()].flatMap((record) =>
          record.issue ? [record.issue] : [],
        ),
      });
  }
  function changeReview(record: MoveRecord, review: ReviewState) {
    if (!record.issue || !owns(record)) return;
    record.issue = { ...record.issue, review };
    present();
  }
  function remove(record: MoveRecord) {
    if (!owns(record)) return;
    record.controller?.abort();
    scope.records.delete(record.source.id);
    present();
  }
  async function publish(record: MoveRecord, model: ModelRead) {
    if (!owns(record)) return;
    const queryKey = queryKeys.model(model.id);
    await client.cancelQueries({ queryKey, exact: true });
    if (!owns(record)) return;
    client.setQueryData<ModelRead>(queryKey, (previous) =>
      previous && previous.edit_version > model.edit_version ? previous : model,
    );
    await onConfirmed();
  }
  async function execute(record: MoveRecord, version: number) {
    if (!owns(record)) return;
    changeReview(record, { phase: "saving" });
    // Returning after navigation must recheck an in-flight command even when its ACK was lost.
    void client.invalidateQueries({
      queryKey: queryKeys.model(record.source.id),
      exact: true,
      refetchType: "none",
    });
    let model: ModelRead;
    try {
      model = await updateModel(
        record.source.id,
        { collection: record.destination ?? "" },
        version,
      );
      if (
        model.id !== record.source.id ||
        !Number.isSafeInteger(model.edit_version) ||
        model.edit_version <= version
      )
        throw new Error("Invalid move acknowledgement");
    } catch (error) {
      if (!owns(record)) return;
      if (
        error instanceof ApiError &&
        error.status >= 400 &&
        error.status < 500 &&
        ![408, 412].includes(error.status)
      ) {
        toast.error(error);
        remove(record);
      } else {
        record.issue = {
          source: record.source,
          destination: record.destination,
          problem: error instanceof ApiError && error.status === 412 ? "conflict" : "unconfirmed",
          review: { phase: "needs-review" },
        };
        present();
      }
      return;
    }
    await publish(record, model);
    if (!owns(record)) return;
    toast.success(uiText("Moved"));
    remove(record);
  }
  async function move(source: ModelDrag, destination: string | null) {
    if (
      !current() ||
      source.session !== session ||
      scope.records.has(source.id) ||
      source.collection === destination
    )
      return;
    const record: MoveRecord = {
      source: { ...source },
      destination,
      issue: null,
      controller: null,
    };
    scope.records.set(source.id, record);
    await execute(record, source.edit_version);
  }
  async function review(id: number) {
    const record = scope.records.get(id);
    if (
      !record?.issue ||
      !owns(record) ||
      ["reviewing", "saving"].includes(record.issue.review.phase)
    )
      return;
    const controller = new AbortController();
    record.controller = controller;
    changeReview(record, { phase: "reviewing" });
    try {
      const model = await getModel(id, { signal: controller.signal });
      if (controller.signal.aborted || !owns(record)) return;
      if (model.id !== id || !Number.isSafeInteger(model.edit_version) || model.edit_version < 1)
        throw new Error("Invalid move review");
      changeReview(record, { phase: "reviewed", model });
    } catch (error) {
      if (controller.signal.aborted || !owns(record)) return;
      changeReview(record, {
        phase:
          error instanceof ApiError && [401, 403, 404].includes(error.status)
            ? "denied"
            : "review-failed",
      });
    }
  }
  async function retry(id: number) {
    const record = scope.records.get(id);
    const state = record?.issue?.review;
    if (
      !record ||
      !owns(record) ||
      state?.phase !== "reviewed" ||
      (state.model.effective_role !== "admin" && state.model.effective_role !== "edit")
    )
      return;
    await execute(record, state.model.edit_version);
  }
  async function adopt(id: number) {
    const record = scope.records.get(id);
    const state = record?.issue?.review;
    if (!record || !owns(record) || state?.phase !== "reviewed") return;
    changeReview(record, { phase: "saving" });
    await publish(record, state.model);
    remove(record);
  }
  function dismiss(id: number) {
    const record = scope.records.get(id);
    if (record && record.issue?.review.phase !== "saving") remove(record);
  }
  return {
    issues: presentation?.scope === scope && entry.session === session ? presentation.issues : [],
    move,
    review,
    retry,
    adopt,
    dismiss,
  };
}
