import {
  type InfiniteData,
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { getModel, getModelPrintJobs, listModels } from "@/lib/api/models";
import { listMultipartModels } from "@/lib/api/multipart-models";
import { listExternalLibraries } from "@/lib/api/libraries";
import {
  cancelSimilarityRun,
  decideSimilarity,
  findModelSimilar,
  getSimilarityCandidate,
  getSimilarityRun,
  getSimilarityStatus,
  listSimilarityCandidates,
  listSimilarityRuns,
  previewSimilaritySelection,
  saveSimilaritySettings,
  searchSimilarModels,
  startSimilarityRun,
} from "@/lib/api/similarity";
import { parseApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { isSimilarityRunActive } from "@/lib/similarity";
import type {
  SimilarityFilters,
  SimilaritySettings,
  SimilarityStatus,
  SimilarityDecision,
  SimilarityRun,
} from "@/types/similarity";

export const similarityKeys = {
  all: ["similarity"] as const,
  status: ["similarity", "status"] as const,
  candidates: ["similarity", "candidates"] as const,
  candidate: (id: number) => ["similarity", "candidate", id] as const,
  run: (id: number | null) => ["similarity", "run", id] as const,
  runs: ["similarity", "runs"] as const,
};
export function similarityStatusOptions() {
  return queryOptions({
    queryKey: similarityKeys.status,
    queryFn: ({ signal }) => getSimilarityStatus({ signal }),
  });
}
export function similarityCandidateOptions(id: number, enabled = true) {
  return queryOptions({
    queryKey: similarityKeys.candidate(id),
    queryFn: ({ signal }) => getSimilarityCandidate(id, { signal }),
    enabled,
  });
}
export function similarityRunOptions(id: number | null) {
  return queryOptions({
    queryKey: similarityKeys.run(id),
    queryFn: ({ signal }) => {
      if (id === null) throw new Error("Similarity run id is required");
      return getSimilarityRun(id, { signal });
    },
    enabled: id !== null,
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      (!query.state.data || isSimilarityRunActive(query.state.data))
        ? 1500
        : false,
  });
}
export function similarityCandidatesOptions(
  filters: SimilarityFilters,
  modelId?: number,
  active = false,
) {
  return infiniteQueryOptions({
    queryKey: [...similarityKeys.candidates, modelId, filters],
    queryFn: ({ pageParam, signal }: { pageParam: string | null; signal: AbortSignal }) =>
      listSimilarityCandidates(
        { ...filters, model_id: modelId, cursor: pageParam ?? undefined, limit: 25 },
        { signal },
      ),
    initialPageParam: null,
    getNextPageParam: (page) => page.next_cursor,
    refetchInterval: (query) => (active && query.state.status !== "error" ? 1500 : false),
  });
}
export function similarityHistoryOptions() {
  return infiniteQueryOptions({
    queryKey: similarityKeys.runs,
    queryFn: ({ pageParam, signal }: { pageParam: number | undefined; signal: AbortSignal }) =>
      listSimilarityRuns(pageParam, { signal }),
    initialPageParam: undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      query.state.data?.pages.some((page) => page.items.some(isSimilarityRunActive))
        ? 1500
        : false,
  });
}
export function similaritySelectionOptions(
  selection: Pick<SimilaritySettings, "minimum_confidence" | "class_overrides">,
) {
  return queryOptions({
    queryKey: ["similarity", "selection-preview", selection],
    queryFn: ({ signal }) => previewSimilaritySelection(selection, { signal }),
  });
}
export type NeighborQuery = Parameters<typeof searchSimilarModels>[0];
export function semanticNeighborsOptions(query: NeighborQuery | null) {
  return queryOptions({
    queryKey: ["similarity", "semantic", query],
    queryFn: ({ signal }) => {
      if (query === null) throw new Error("Semantic query is required");
      return searchSimilarModels(query, { signal });
    },
    enabled: query !== null,
    gcTime: 0,
    retry: false,
  });
}
// Canonical library readers remain injected seams pending the coordinator's M10 owner cutover.
export function similarityModelOptions(id: number, read = getModel) {
  return queryOptions({
    queryKey: queryKeys.model(id),
    queryFn: ({ signal }) => read(id, { signal }),
  });
}
export function similarityPrintJobsOptions(id: number, read = getModelPrintJobs) {
  return queryOptions({
    queryKey: ["model-print-jobs", id],
    queryFn: ({ signal }) => read(id, { signal }),
  });
}
export function similarityModelChoicesOptions(
  q: string,
  offset: number,
  enabled: boolean,
  read = listModels,
) {
  return queryOptions({
    queryKey: ["similarity", "model-choices", q, offset],
    queryFn: ({ signal }) => read({ q, limit: 25, offset }, { signal }),
    enabled,
  });
}
type SimilaritySources =
  | { kind: "enabled"; items: Awaited<ReturnType<typeof listExternalLibraries>> }
  | { kind: "disabled" };
export function similaritySourcesOptions(read = listExternalLibraries) {
  return queryOptions({
    queryKey: ["similarity", "sources"],
    queryFn: async ({ signal }): Promise<SimilaritySources> => {
      try {
        return { kind: "enabled", items: await read({ signal }) };
      } catch (error) {
        const failure = parseApiError(error);
        if (failure.status === 404 && failure.code === "feature_disabled")
          return { kind: "disabled" };
        throw error;
      }
    },
  });
}
export function similarityTargetsOptions(q: string, enabled: boolean, read = listMultipartModels) {
  return infiniteQueryOptions({
    queryKey: ["similarity", "multipart-targets", q],
    queryFn: ({ pageParam, signal }) => read({ q, limit: 30, offset: pageParam }, { signal }),
    initialPageParam: 0,
    getNextPageParam: (page, pages) => (page.length === 30 ? pages.length * 30 : undefined),
    enabled,
  });
}

/** Similarity acknowledgements own publication; review identity/version stay captured in the gesture. */
export function useSimilarityCommands() {
  const client = useQueryClient();
  const reconciliationRoots = [
    similarityKeys.status,
    similarityKeys.candidates,
    ["similarity", "candidate"],
    similarityKeys.runs,
    ["similarity", "run"],
    ["similarity", "multipart-targets"],
  ];
  const cancelReads = () =>
    client.cancelQueries({
      predicate: (query) =>
        reconciliationRoots.some((root) =>
          root.every((part, index) => query.queryKey[index] === part),
        ),
    });
  const prepare = async (session: number) => {
    requireSessionVersion(session);
    await cancelReads();
    requireSessionVersion(session);
    return session;
  };
  const reconcile = async (session: number | undefined) => {
    if (session === undefined) throw new Error("Similarity command session is required");
    await cancelReads();
    requireSessionVersion(session);
    return session;
  };
  const publishRun = (run: SimilarityRun, session: number) => {
    requireSessionVersion(session);
    client.setQueryData(similarityKeys.run(run.id), run);
    client.setQueryData<
      InfiniteData<{ items: SimilarityRun[]; next_cursor: number | null }, number | undefined>
    >(similarityKeys.runs, (history) => {
      requireSessionVersion(session);
      return history
        ? {
            ...history,
            pages: history.pages.map((page) => ({
              ...page,
              items: page.items.map((item) => (item.id === run.id ? run : item)),
            })),
          }
        : history;
    });
  };
  const refresh = (session: number) => {
    requireSessionVersion(session);
    for (const queryKey of [
      similarityKeys.status,
      similarityKeys.runs,
      similarityKeys.candidates,
      ["similarity", "run"],
    ]) {
      requireSessionVersion(session);
      void client.invalidateQueries({ queryKey });
    }
  };
  return {
    saveSettings: useMutation({
      retry: false,
      mutationFn: ({
        payload,
        session,
      }: {
        payload: Partial<SimilaritySettings>;
        session: number;
      }) => {
        requireSessionVersion(session);
        return saveSimilaritySettings(payload);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (settings, _, context) => {
        const session = await reconcile(context);
        client.setQueryData<SimilarityStatus>(similarityKeys.status, (status) => {
          requireSessionVersion(session);
          return status
            ? {
                ...status,
                settings,
                enabled: settings.enabled,
                embeddings_enabled: settings.embeddings_enabled,
              }
            : status;
        });
        refresh(session);
      },
    }),
    start: useMutation({
      retry: false,
      mutationFn: ({
        scope,
        ids,
        session,
      }: {
        scope: SimilarityRun["scope"];
        ids: number[];
        session: number;
      }) => {
        requireSessionVersion(session);
        return startSimilarityRun(scope, ids);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (run, _, context) => {
        const session = await reconcile(context);
        publishRun(run, session);
        refresh(session);
      },
      onError: (_error, variables) => {
        if (variables.session === getSessionVersion()) refresh(variables.session);
      },
    }),
    cancel: useMutation({
      retry: false,
      mutationFn: ({ id, session }: { id: number; session: number }) => {
        requireSessionVersion(session);
        return cancelSimilarityRun(id);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (run, _, context) => {
        const session = await reconcile(context);
        publishRun(run, session);
        refresh(session);
      },
    }),
    find: useMutation({
      retry: false,
      mutationFn: ({ id, session }: { id: number; session: number }) => {
        requireSessionVersion(session);
        return findModelSimilar(id);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (result, _, context) => {
        const session = await reconcile(context);
        publishRun(result.run, session);
        refresh(session);
      },
    }),
    decide: useMutation({
      retry: false,
      mutationFn: ({
        id,
        payload,
        session,
      }: {
        id: number;
        payload: SimilarityDecision;
        session: number;
      }) => {
        requireSessionVersion(session);
        return decideSimilarity(id, payload);
      },
      onMutate: ({ session }) => prepare(session),
      onError: (_error, { id, session }) => {
        if (session === getSessionVersion())
          void client.invalidateQueries({ queryKey: similarityKeys.candidate(id) });
      },
      onSuccess: async (result, _, context) => {
        const session = await reconcile(context);
        client.setQueryData(similarityKeys.candidate(result.candidate.id), result.candidate);
        refresh(session);
        if (result.resolution_kind === "multipart") {
          requireSessionVersion(session);
          void client.invalidateQueries({ queryKey: ["similarity", "multipart-targets"] });
        }
        for (const queryKey of [
          queryKeys.models,
          queryKeys.multipartModels,
          queryKeys.outliner,
          queryKeys.vaultStats,
        ]) {
          requireSessionVersion(session);
          await client.cancelQueries({ queryKey });
          requireSessionVersion(session);
          void client.invalidateQueries({ queryKey });
        }
      },
    }),
  };
}
