import { refreshLibraryMetadata } from "./metadata";
import { acceptsEditingSnapshot } from "./editing";
import { useCallback, useState, useSyncExternalStore } from "react";
import { queryOptions, useQuery, useQueryClient } from "@tanstack/react-query";
import { getModel, getModelPrinterFiles, getModelPrintJobs } from "@/lib/api/models";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
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
      client.setQueryData<ModelRead>(queryKeys.model(id), (current) => {
        const candidate = next instanceof Function ? (current ? next(current) : undefined) : next;
        if (!candidate || candidate.id !== id) return current;
        if (!acceptsEditingSnapshot(current, candidate, observedEpoch)) return current;
        accepted = true;
        return candidate;
      });
      if (accepted) refreshLibraryMetadata(client, session, { kind: "model", id });
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
