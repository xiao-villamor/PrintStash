import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { starModel, unstarModel } from "@/lib/api/models";
import { starMultipartModel, unstarMultipartModel } from "@/lib/api/multipart-models";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import { libraryBrowseKeys } from "./browse";
import type { LibraryBrowseEntry, LibraryBrowsePage } from "@/types/library-browse";
import type { ModelRead, MultipartModelRead } from "@/types";

interface StarCommand {
  kind: "model" | "multipart";
  id: number;
  starred: boolean;
}

function matches(entry: LibraryBrowseEntry, command: StarCommand): boolean {
  return (
    entry.kind === command.kind &&
    (entry.kind === "model" ? entry.model.id : entry.multipart.id) === command.id
  );
}

/** Publish the server acknowledgement after retiring every earlier read. */
export function useLibraryStar() {
  const client = useQueryClient();
  const detailKey = (command: StarCommand) =>
    command.kind === "model" ? queryKeys.model(command.id) : queryKeys.multipartModel(command.id);
  const cancel = (command: StarCommand) =>
    Promise.all([
      client.cancelQueries({ queryKey: libraryBrowseKeys.all }),
      client.cancelQueries({ queryKey: detailKey(command) }),
    ]);
  const mutation = useMutation({
    retry: false,
    mutationFn: async (command: StarCommand & { version: number }) => {
      requireSessionVersion(command.version);
      const result =
        command.kind === "model"
          ? await (command.starred ? starModel(command.id) : unstarModel(command.id))
          : await (command.starred
              ? starMultipartModel(command.id)
              : unstarMultipartModel(command.id));
      requireSessionVersion(command.version);
      return result.starred;
    },
    onMutate: async (command) => {
      requireSessionVersion(command.version);
      await cancel(command);
      requireSessionVersion(command.version);
    },
    onSuccess: async (starred, command) => {
      requireSessionVersion(command.version);
      await cancel(command);
      requireSessionVersion(command.version);
      client.setQueriesData<InfiniteData<LibraryBrowsePage>>(
        { queryKey: libraryBrowseKeys.all },
        (data) =>
          data && {
            ...data,
            pages: data.pages.map((page) => ({
              ...page,
              items: page.items.map((entry) => {
                if (!matches(entry, command)) return entry;
                return entry.kind === "model"
                  ? { ...entry, model: { ...entry.model, starred } }
                  : { ...entry, multipart: { ...entry.multipart, starred } };
              }),
            })),
          },
      );
      requireSessionVersion(command.version);
      if (!starred)
        client.setQueriesData<InfiniteData<LibraryBrowsePage>>(
          { queryKey: [...libraryBrowseKeys.all, { favorites: true }] },
          (data) => {
            if (!data) return data;
            const removed = data.pages.some((page) =>
              page.items.some((entry) => matches(entry, command)),
            );
            return {
              ...data,
              pages: data.pages.map((page) => ({
                ...page,
                total: page.total - (removed ? 1 : 0),
                items: page.items.filter((entry) => !matches(entry, command)),
              })),
            };
          },
        );
      requireSessionVersion(command.version);
      client.setQueryData<ModelRead | MultipartModelRead>(
        detailKey(command),
        (data) => data && { ...data, starred },
      );
    },
  });
  // Capture the session at the gesture, before asynchronous onMutate cancellation.
  return {
    ...mutation,
    mutate: (command: StarCommand) => mutation.mutate({ ...command, version: getSessionVersion() }),
    mutateAsync: (command: StarCommand) =>
      mutation.mutateAsync({ ...command, version: getSessionVersion() }),
  };
}
