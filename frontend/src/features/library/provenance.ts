import { acceptsEditingSnapshot } from "./editing";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModel, updateModel } from "@/lib/api/models";
import {
  deleteModelSourceCover,
  getModelProvenance,
  getModelSourceCoverContentPath,
  patchModelProvenance,
  putModelSourceCover,
} from "@/lib/api/provenance";
import { onAuthChange } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { ModelProvenancePatch, ModelProvenanceRead, ModelRead } from "@/types";

export const sourceApi = {
  getProvenance: getModelProvenance,
  patchProvenance: patchModelProvenance,
  getModel,
  updateModel,
  putCover: putModelSourceCover,
  deleteCover: deleteModelSourceCover,
  getCoverContentPath: getModelSourceCoverContentPath,
};
export type SourceApi = typeof sourceApi;

export function provenanceOptions(id: number, read = getModelProvenance) {
  return queryOptions({
    queryKey: [...queryKeys.model(id), "provenance"],
    queryFn: ({ signal }) => read(id, { signal }),
    retry: false,
  });
}

export type SourceCommand =
  | { kind: "override"; sourceId: number; payload: ModelProvenancePatch }
  | { kind: "apply"; payload: { name: string } | { description: string } }
  | { kind: "upload"; sourceId: number; file: File }
  | { kind: "delete"; sourceId: number };

type PendingEdit = { command: SourceCommand; base: ModelProvenanceRead; finish: () => void };
type ReviewedSource = {
  model: ModelRead;
  provenance: ModelProvenanceRead;
  adopt: () => Promise<void>;
};
type EditingState =
  | { phase: "idle" }
  | { phase: "saving"; pending: PendingEdit }
  | {
      phase: "blocked";
      pending: PendingEdit;
      problem: "conflict" | "unconfirmed";
      reviewed: ReviewedSource | null;
    };

function readDenied(error: Error | null): boolean {
  return error instanceof ApiError && [401, 403, 404].includes(error.status);
}

/** One Source snapshot; command text/files remain local until an acknowledged write. */
export function useSourceEditing(id: number, api: SourceApi = sourceApi) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const mounted = useRef(true);
  const working = useRef(false);
  const reviewRequest = useRef<AbortController | null>(null);
  const [state, setState] = useState<EditingState>({ phase: "idle" });
  const [reviewing, setReviewing] = useState(false);
  const [denied, setDenied] = useState(false);
  const [editDenied, setEditDenied] = useState(false);
  const active = session === currentSession;
  const query = useQuery({ ...provenanceOptions(id, api.getProvenance), enabled: active });
  const isCurrent = useCallback(
    () => mounted.current && session === getSessionVersion(),
    [session],
  );
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      reviewRequest.current?.abort();
    };
  }, []);

  const observedSourceEpoch = query.data?.edit_epoch ?? null;
  const observedModelEpoch =
    client.getQueryData<ModelRead>(queryKeys.model(id))?.edit_epoch ?? null;

  async function publish(next: ModelProvenanceRead) {
    if (!isCurrent()) return;
    await client.cancelQueries({ queryKey: provenanceOptions(id).queryKey, exact: true });
    if (!isCurrent()) return;
    const current = client.getQueryData<ModelProvenanceRead>(provenanceOptions(id).queryKey);
    if (
      current &&
      current.edit_epoch !== next.edit_epoch &&
      !acceptsEditingSnapshot(current, next, observedSourceEpoch)
    )
      throw new Error("Editing history changed during publication");
    client.setQueryData<ModelProvenanceRead>(provenanceOptions(id).queryKey, (current) =>
      acceptsEditingSnapshot(current, next, observedSourceEpoch) ? next : current,
    );
  }

  async function publishModel(next: ModelRead) {
    if (!isCurrent()) return;
    await client.cancelQueries({ queryKey: queryKeys.model(id), exact: true });
    if (!isCurrent()) return;
    const current = client.getQueryData<ModelRead>(queryKeys.model(id));
    if (
      current &&
      current.edit_epoch !== next.edit_epoch &&
      !acceptsEditingSnapshot(current, next, observedModelEpoch)
    )
      throw new Error("Editing history changed during publication");
    client.setQueryData<ModelRead>(queryKeys.model(id), (current) =>
      acceptsEditingSnapshot(current, next, observedModelEpoch) ? next : current,
    );
  }

  async function execute(pending: PendingEdit) {
    if (working.current || !isCurrent()) return;
    working.current = true;
    setState({ phase: "saving", pending });
    // A departed editor cannot publish feedback. Mark its read stale now so
    // returning to Source still checks the server after a lost acknowledgement.
    void client.invalidateQueries({
      queryKey: provenanceOptions(id).queryKey,
      exact: true,
      refetchType: "none",
    });
    const { command, base } = pending;
    try {
      if (command.kind === "override") {
        await publish(await api.patchProvenance(id, command.sourceId, command.payload, base));
      } else if (command.kind === "apply") {
        const next = await api.updateModel(id, command.payload, base);
        if (
          next.id !== id ||
          !Number.isSafeInteger(next.edit_version) ||
          next.edit_version <= base.edit_version
        )
          throw new Error("Invalid Model acknowledgement");
        await publishModel(next);
        await publish({ ...base, edit_version: next.edit_version });
      } else {
        const receipt =
          command.kind === "upload"
            ? await api.putCover(id, command.sourceId, command.file, base)
            : await api.deleteCover(id, command.sourceId, base);
        await publish({
          ...base,
          edit_version: receipt.edit_version,
          sources: base.sources.map((source) =>
            source.id === command.sourceId ? { ...source, cover: receipt.cover } : source,
          ),
        });
      }
      if (!isCurrent()) return;
      // Source edits also advance the Model editing base. A fresh detail is
      // required before another editor opens; do not invent other Model fields.
      if (command.kind !== "apply")
        void client.invalidateQueries({ queryKey: queryKeys.model(id), exact: true });
      pending.finish();
      if (command.kind === "apply") toast.success(uiText("source.modelUpdated"));
      setState({ phase: "idle" });
    } catch (error) {
      if (!isCurrent()) return;
      const conflict = error instanceof ApiError && error.status === 412;
      const unknownOutcome =
        !(error instanceof ApiError) ||
        error.status === 0 ||
        error.status === 408 ||
        error.status >= 500;
      if (conflict || unknownOutcome)
        setState({
          phase: "blocked",
          pending,
          problem: conflict ? "conflict" : "unconfirmed",
          reviewed: null,
        });
      else {
        setState({ phase: "idle" });
        if (error instanceof Error && readDenied(error)) {
          setDenied(true);
          setEditDenied(true);
        }
        toast.error(error);
      }
    } finally {
      working.current = false;
    }
  }

  function submit(command: SourceCommand, base: ModelProvenanceRead, finish: () => void) {
    if (state.phase !== "idle" || editDenied || denied) return;
    void execute({ command, base, finish });
  }

  async function review() {
    if (state.phase !== "blocked" || working.current || !isCurrent()) return;
    working.current = true;
    setReviewing(true);
    setState({ ...state, reviewed: null });
    reviewRequest.current?.abort();
    const controller = new AbortController();
    reviewRequest.current = controller;
    try {
      const [model, provenance] = await Promise.all([
        api.getModel(id, { signal: controller.signal }),
        api.getProvenance(id, { signal: controller.signal }),
      ]);
      if (!isCurrent() || controller.signal.aborted) return;
      if (
        model.id !== id ||
        model.edit_version !== provenance.edit_version ||
        model.edit_epoch !== provenance.edit_epoch
      ) {
        toast.error(uiText("source.reviewChanged"));
        return;
      }
      setDenied(false);
      const command = state.pending.command;
      const sourceMissing =
        command.kind !== "apply" &&
        !provenance.sources.some((source) => source.id === command.sourceId);
      setEditDenied(
        sourceMissing || (model.effective_role !== "edit" && model.effective_role !== "admin"),
      );
      setState({
        ...state,
        reviewed: {
          model,
          provenance,
          adopt: async () => {
            await publish(provenance);
            await publishModel(model);
          },
        },
      });
    } catch (error) {
      if (!isCurrent() || controller.signal.aborted) return;
      if (error instanceof Error && readDenied(error)) setDenied(true);
      else toast.error(error);
    } finally {
      controller.abort();
      working.current = false;
      if (isCurrent()) setReviewing(false);
    }
  }

  function retry() {
    if (state.phase !== "blocked" || !state.reviewed || editDenied || denied || reviewing) return;
    void execute({ ...state.pending, base: state.reviewed.provenance });
  }

  async function adopt() {
    if (state.phase !== "blocked" || !state.reviewed || working.current || !isCurrent()) return;
    working.current = true;
    try {
      await state.reviewed.adopt();
      if (!isCurrent()) return;
      state.pending.finish();
      setState({ phase: "idle" });
    } catch (error) {
      if (isCurrent()) {
        setState({ ...state, reviewed: null });
        toast.error(error);
      }
    } finally {
      working.current = false;
    }
  }

  return {
    query,
    data: active ? query.data : undefined,
    active,
    state,
    reviewing,
    editDenied,
    denied: denied || readDenied(query.error),
    submit,
    review,
    retry,
    adopt,
    retryRead: () => {
      if (isCurrent())
        void query.refetch().then((result) => {
          if (isCurrent() && result.isSuccess) setDenied(false);
        });
    },
  };
}
