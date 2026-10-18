import { refreshLibraryMetadata } from "./metadata";
import { acceptsEditingSnapshot } from "./editing";
import { useCallback, useState, useSyncExternalStore } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModel, getModelPrinterFiles, getModelPrintJobs } from "@/lib/api/models";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import { useIntentPrefetch } from "@/lib/use-intent-prefetch";
import type { ModelRead, ModelPrintJobRead } from "@/types";

export function modelDetailOptions(id: number | null) {
  return queryOptions({
    queryKey: id === null ? [...queryKeys.models, "unselected"] : queryKeys.model(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Model id is required");
      return getModel(id, { signal });
    },
    enabled: id !== null,
    staleTime: 60_000,
    retry: false,
  });
}

/**
 * Warm the detail read of the Model under a sustained pointer or focus, so
 * opening it lands on data that is already there. It fills exactly the entry
 * the detail page reads; an intent that ends first cancels a read nobody
 * observes, and mesh bytes are never part of it.
 */
export function useModelOpenIntent(enabled: boolean): (id: number | null) => void {
  const client = useQueryClient();
  const intend = useIntentPrefetch((target, signal) => {
    const options = modelDetailOptions(Number(target));
    const cancel = () => {
      const query = client.getQueryCache().find({ queryKey: options.queryKey, exact: true });
      if (query && query.getObserversCount() === 0)
        void client.cancelQueries({ queryKey: options.queryKey, exact: true });
    };
    signal.addEventListener("abort", cancel, { once: true });
    void client.prefetchQuery(options).finally(() => signal.removeEventListener("abort", cancel));
  }, enabled);
  return useCallback((id) => intend(id === null ? null : String(id)), [intend]);
}

export function modelPrinterFilesOptions(id: number) {
  return queryOptions({
    queryKey: [...queryKeys.model(id), "printer-files"],
    queryFn: ({ signal }) => getModelPrinterFiles(id, { signal }),
    retry: false,
  });
}

export function modelPrintJobsOptions(id: number) {
  return queryOptions({
    queryKey: [...queryKeys.model(id), "print-jobs"],
    queryFn: ({ signal }) => getModelPrintJobs(id, { signal }),
    retry: false,
  });
}

export type ModelPublication = ModelRead | ((current: ModelRead) => ModelRead);
export type PublishModel = (next: ModelPublication) => Promise<boolean>;

/** The detail, its event reads and confirmed writes share one session-scoped record. */
export function useModelDetail(id: number, initialModel?: ModelRead) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const active = session === currentSession;
  const query = useQuery({
    ...modelDetailOptions(id),
    initialData: active ? initialModel : undefined,
    enabled: active,
  });
  const observedEpoch = query.data?.edit_epoch ?? null;
  const publish = useCallback<PublishModel>(
    async (next) => {
      if (session !== getSessionVersion()) return false;
      await client.cancelQueries({ queryKey: queryKeys.model(id), exact: true });
      if (session !== getSessionVersion()) return false;
      let accepted = false;
      let derivativesChanged = false;
      client.setQueryData<ModelRead>(queryKeys.model(id), (current) => {
        const candidate = next instanceof Function ? (current ? next(current) : undefined) : next;
        if (!candidate || candidate.id !== id) return current;
        if (!acceptsEditingSnapshot(current, candidate, observedEpoch)) return current;
        derivativesChanged =
          current?.edit_epoch === candidate.edit_epoch &&
          current.derivatives_revision !== candidate.derivatives_revision;
        accepted = true;
        return candidate;
      });
      if (accepted) {
        refreshLibraryMetadata(client, session, { kind: "model", id });
        // Edit counters order edits, while derivative revisions only name a
        // snapshot. A late receipt can replace a completed background read;
        // revalidate differing revisions rather than ordering the opaque token.
        if (derivativesChanged)
          void client.invalidateQueries({ queryKey: queryKeys.model(id), exact: true });
      }
      return accepted;
    },
    [client, id, session, observedEpoch],
  );
  return { ...query, data: active ? query.data : undefined, active, publish };
}

/** A confirmed manual/imported print becomes visible without competing with an old history read. */
export function useModelPrintJobs(id: number, enabled: boolean) {
  const client = useQueryClient();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const query = useQuery({
    ...modelPrintJobsOptions(id),
    enabled: enabled && session === currentSession,
  });
  const publish = useCallback(
    async (job: ModelPrintJobRead) => {
      if (session !== getSessionVersion()) return;
      const queryKey = modelPrintJobsOptions(id).queryKey;
      await client.cancelQueries({ queryKey, exact: true });
      if (session !== getSessionVersion()) return;
      client.setQueryData<ModelPrintJobRead[]>(queryKey, (jobs) => [
        job,
        ...(jobs ?? []).filter((item) => item.id !== job.id),
      ]);
    },
    [client, id, session],
  );
  return { ...query, publish };
}
