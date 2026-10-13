import { captureEditingBase } from "@/lib/api/editing";
import type { EditingBase } from "@/types/editing";
import { useEffect, useRef } from "react";
import {
  infiniteQueryOptions,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { listJobs } from "@/lib/api/jobs";
import {
  MODEL_DOWNLOAD_KIND,
  actOnSearchGeneration,
  cancelInferenceDownload,
  createInferenceEndpoint,
  deleteInferenceModel,
  downloadInferenceModel,
  estimateSearchGeneration,
  getSearchPreferences,
  getSearchSettings,
  getSearchStatus,
  importEnvironmentEndpoint,
  listInferenceModels,
  listSearchGenerations,
  parseSearch,
  prepareSearchGeneration,
  saveSearchPreferences,
  saveSearchSettings,
  searchImage,
  searchLibrary,
  searchUsingModel,
  validateInferenceModel,
  type SearchQuery,
} from "@/lib/api/search";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import type {
  EndpointProposal,
  GenerationProposal,
  SearchGeneration,
  SearchSettings,
  SearchSettingsRead,
} from "@/types/search";

export const searchKeys = {
  all: ["ai-search"] as const,
  status: (userId: number | undefined) => ["ai-search", "status", userId] as const,
  settings: ["ai-search", "settings"] as const,
  models: ["ai-search", "models"] as const,
  generations: ["ai-search", "generations"] as const,
  downloads: ["ai-search", "downloads"] as const,
  preferences: (userId: number | undefined) => ["search-preferences", userId] as const,
};
export function searchStatusOptions(userId: number | undefined, enabled = true) {
  return queryOptions({
    queryKey: searchKeys.status(userId),
    queryFn: ({ signal }) => getSearchStatus({ signal }),
    enabled: userId !== undefined && enabled,
    refetchInterval: (query) => (query.state.status === "error" ? false : 15000),
  });
}
export function searchSuggestionsOptions(userId: number | undefined, q: string, enabled: boolean) {
  return queryOptions({
    queryKey: ["search-suggestions", userId, q],
    queryFn: ({ signal }) => searchLibrary({ q, mode: "lexical", instant: true, limit: 5 }, signal),
    enabled: userId !== undefined && enabled,
    gcTime: 0,
    retry: false,
  });
}
export function searchSettingsOptions() {
  return queryOptions({
    queryKey: searchKeys.settings,
    staleTime: 0,
    queryFn: ({ signal }) => getSearchSettings({ signal }),
  });
}
export function inferenceModelsOptions(enabled = true) {
  return queryOptions({
    queryKey: searchKeys.models,
    queryFn: ({ signal }) => listInferenceModels({ signal }),
    enabled,
  });
}
export function searchGenerationsOptions(enabled = true) {
  return queryOptions({
    queryKey: searchKeys.generations,
    queryFn: ({ signal }) => listSearchGenerations({ signal }),
    enabled,
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      query.state.data?.some(
        (generation) =>
          generation.state === "building" &&
          generation.phase !== "ready" &&
          generation.phase !== "verify_failed",
      )
        ? 3000
        : false,
  });
}
export function inferenceDownloadsOptions(enabled = true) {
  return queryOptions({
    queryKey: searchKeys.downloads,
    queryFn: async ({ signal }) =>
      (await listJobs([], { signal })).filter((job) => job.kind === MODEL_DOWNLOAD_KIND),
    enabled,
    refetchInterval: (query) =>
      query.state.status !== "error" &&
      query.state.data?.some((job) => job.state === "running" || job.state === "queued")
        ? 1500
        : false,
  });
}
/** One mounted catalog owner reconciles completed downloads across setup and advanced consumers. */
export function useAiSearchCatalog(enabled: boolean) {
  const client = useQueryClient();
  const session = useRef(getSessionVersion());
  const models = useQuery(inferenceModelsOptions(enabled));
  const generations = useQuery(searchGenerationsOptions(enabled));
  const downloads = useQuery(inferenceDownloadsOptions(enabled));
  const completed =
    downloads.data
      ?.filter((job) => job.state === "completed")
      .map((job) => job.job_id)
      .sort()
      .join(",") ?? "";
  useEffect(() => {
    if (enabled && completed && session.current === getSessionVersion())
      void client.invalidateQueries({ queryKey: searchKeys.models });
  }, [client, completed, enabled]);
  return { models, generations, downloads };
}
export type AiSearchCatalog = ReturnType<typeof useAiSearchCatalog>;
export function searchPreferencesOptions(userId: number | undefined, enabled = true) {
  return queryOptions({
    queryKey: searchKeys.preferences(userId),
    queryFn: ({ signal }) => getSearchPreferences({ signal }),
    enabled: userId !== undefined && enabled,
    retry: false,
  });
}
export function parsedSearchOptions(userId: number | undefined, q: string, enabled: boolean) {
  return queryOptions({
    queryKey: ["search-parse", userId, q],
    queryFn: ({ signal }) => parseSearch(q, signal),
    enabled,
    retry: false,
    gcTime: 0,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  });
}
export type SearchIntent =
  | { kind: "text"; query: SearchQuery }
  | { kind: "model"; modelId: number }
  | { kind: "image"; image: File; identity: string }
  | { kind: "idle" };
export function searchResultsOptions(
  userId: number | undefined,
  intent: SearchIntent,
  enabled: boolean,
) {
  const identity =
    intent.kind === "image" ? { kind: intent.kind, identity: intent.identity } : intent;
  return infiniteQueryOptions({
    queryKey: ["search-results", userId, identity],
    queryFn: ({ pageParam, signal }: { pageParam: string | undefined; signal: AbortSignal }) => {
      if (intent.kind === "model") return searchUsingModel(intent.modelId, pageParam, signal);
      if (intent.kind === "image") return searchImage(intent.image, { cursor: pageParam }, signal);
      if (intent.kind === "text")
        return searchLibrary({ ...intent.query, cursor: pageParam }, signal);
      throw new Error("Search intent is required");
    },
    initialPageParam: undefined,
    getNextPageParam: (page) => page.next_cursor ?? undefined,
    enabled: userId !== undefined && enabled && intent.kind !== "idle",
    retry: false,
    gcTime: 0,
  });
}
export function generationEstimateOptions(proposal: GenerationProposal | null) {
  return queryOptions({
    queryKey: ["ai-search", "estimate", proposal],
    queryFn: ({ signal }) => {
      if (!proposal) throw new Error("Generation proposal is required");
      return estimateSearchGeneration(proposal, { signal });
    },
    enabled: proposal !== null,
    retry: false,
    gcTime: 0,
  });
}

/** Commands capture a gesture incarnation; each acknowledgement has one publication/refresh owner. */
export function useSearchCommands() {
  const client = useQueryClient();
  const cancel = () => client.cancelQueries({ queryKey: searchKeys.all });
  const prepare = async (session: number) => {
    requireSessionVersion(session);
    await cancel();
    requireSessionVersion(session);
    return session;
  };
  const acknowledge = async (session: number | undefined) => {
    if (session === undefined) throw new Error("Search command session is required");
    requireSessionVersion(session);
    await cancel();
    requireSessionVersion(session);
    return session;
  };
  const refresh = (session: number, keys: readonly (readonly unknown[])[]) => {
    for (const queryKey of keys) {
      requireSessionVersion(session);
      void client.invalidateQueries({ queryKey });
    }
  };
  const publishGeneration = async (generation: SearchGeneration, context: number | undefined) => {
    const session = await acknowledge(context);
    requireSessionVersion(session);
    client.setQueryData<SearchGeneration[]>(searchKeys.generations, (rows) => {
      requireSessionVersion(session);
      return rows?.some((row) => row.id === generation.id)
        ? rows.map((row) => (row.id === generation.id ? generation : row))
        : rows;
    });
    refresh(session, [searchKeys.generations, ["ai-search", "status"]]);
  };
  return {
    reviewSettings: async (session: number) => {
      requireSessionVersion(session);
      await client.cancelQueries({ queryKey: searchKeys.settings, exact: true });
      requireSessionVersion(session);
      const result = await client.fetchQuery({ ...searchSettingsOptions(), retry: false });
      requireSessionVersion(session);
      captureEditingBase(result);
      return result;
    },
    preferences: useMutation({
      retry: false,
      mutationFn: ({
        payload,
        session,
      }: {
        payload: Parameters<typeof saveSearchPreferences>[0];
        session: number;
        userId: number;
      }) => {
        requireSessionVersion(session);
        return saveSearchPreferences(payload);
      },
      onMutate: async ({ session, userId }) => {
        requireSessionVersion(session);
        await client.cancelQueries({ queryKey: searchKeys.preferences(userId) });
        requireSessionVersion(session);
        return session;
      },
      onSuccess: async (result, { userId }, context) => {
        if (context === undefined) throw new Error("Search preferences session is required");
        requireSessionVersion(context);
        await client.cancelQueries({ queryKey: searchKeys.preferences(userId) });
        requireSessionVersion(context);
        client.setQueryData(searchKeys.preferences(userId), result);
      },
    }),
    settings: useMutation({
      retry: false,
      mutationFn: ({
        payload,
        base,
        session,
      }: {
        payload: SearchSettings;
        base: EditingBase;
        session: number;
      }) => {
        requireSessionVersion(session);
        return saveSearchSettings(payload, base);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (result, _, context) => {
        const session = await acknowledge(context);
        requireSessionVersion(session);
        client.setQueryData<SearchSettingsRead>(searchKeys.settings, (current) => {
          requireSessionVersion(session);
          return current &&
            (current.edit_epoch !== result.edit_epoch || current.edit_version > result.edit_version)
            ? current
            : result;
        });
        refresh(session, [["ai-search", "status"], searchKeys.models, searchKeys.generations]);
      },
    }),
    endpoint: useMutation({
      retry: false,
      mutationFn: ({ payload, session }: { payload: EndpointProposal; session: number }) => {
        requireSessionVersion(session);
        return createInferenceEndpoint(payload);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (endpoint, _, context) => {
        const session = await acknowledge(context);
        requireSessionVersion(session);
        client.setQueryData<Awaited<ReturnType<typeof getSearchSettings>>>(
          searchKeys.settings,
          (current) => {
            requireSessionVersion(session);
            return current
              ? {
                  ...current,
                  endpoints: current.endpoints.some((item) => item.id === endpoint.id)
                    ? current.endpoints.map((item) => (item.id === endpoint.id ? endpoint : item))
                    : [...current.endpoints, endpoint],
                }
              : current;
          },
        );
        refresh(session, [searchKeys.settings, ["ai-search", "status"]]);
      },
    }),
    importEndpoint: useMutation({
      retry: false,
      mutationFn: ({ kind, session }: { kind: "embedding" | "chat"; session: number }) => {
        requireSessionVersion(session);
        return importEnvironmentEndpoint(kind);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (_, __, context) => {
        const session = await acknowledge(context);
        refresh(session, [searchKeys.settings]);
      },
    }),
    generation: useMutation({
      retry: false,
      mutationFn: ({ proposal, session }: { proposal: GenerationProposal; session: number }) => {
        requireSessionVersion(session);
        return prepareSearchGeneration(proposal);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: (result, _, context) => publishGeneration(result, context),
    }),
    generationAction: useMutation({
      retry: false,
      mutationFn: ({
        generation,
        action,
        session,
      }: {
        generation: SearchGeneration;
        action: "activate" | "cancel" | "retry";
        session: number;
      }) => {
        requireSessionVersion(session);
        return actOnSearchGeneration(generation, action);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: (result, _, context) => publishGeneration(result, context),
    }),
    download: useMutation({
      retry: false,
      mutationFn: ({ key, session }: { key: string; session: number }) => {
        requireSessionVersion(session);
        return downloadInferenceModel(key);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (_, __, context) => {
        const session = await acknowledge(context);
        refresh(session, [searchKeys.downloads]);
      },
    }),
    cancelDownload: useMutation({
      retry: false,
      mutationFn: ({ id, session }: { id: string; session: number }) => {
        requireSessionVersion(session);
        return cancelInferenceDownload(id);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (_, __, context) => {
        const session = await acknowledge(context);
        refresh(session, [searchKeys.downloads]);
      },
    }),
    validate: useMutation({
      retry: false,
      mutationFn: ({ id, session }: { id: string; session: number }) => {
        requireSessionVersion(session);
        return validateInferenceModel(id);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (_, __, context) => {
        const session = await acknowledge(context);
        refresh(session, [searchKeys.models]);
      },
    }),
    remove: useMutation({
      retry: false,
      mutationFn: ({ id, session }: { id: string; session: number }) => {
        requireSessionVersion(session);
        return deleteInferenceModel(id);
      },
      onMutate: ({ session }) => prepare(session),
      onSuccess: async (_, { id }, context) => {
        const session = await acknowledge(context);
        requireSessionVersion(session);
        client.setQueryData<Awaited<ReturnType<typeof listInferenceModels>>>(
          searchKeys.models,
          (rows) => {
            requireSessionVersion(session);
            return rows?.filter((row) => row.id !== id);
          },
        );
        refresh(session, [searchKeys.models]);
      },
    }),
  };
}
