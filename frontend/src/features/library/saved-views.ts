import { useSyncExternalStore } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getUser, onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import {
  createSavedView,
  deleteSavedView,
  listSavedViews,
  updateSavedView,
} from "@/lib/api/saved-views";
import type { SavedViewFilters, SavedViewRead } from "@/types";

export type SavedViewCommand =
  | { kind: "create"; name: string; filters: SavedViewFilters }
  | { kind: "update"; id: number; payload: { name?: string; filters?: SavedViewFilters } }
  | { kind: "delete"; id: number };

/** One authorized list; complete write acknowledgements replace earlier reads. */
export function useSavedViews(enabled: boolean) {
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const userId = getUser()?.id ?? null;
  const client = useQueryClient();
  const queryKey = ["saved-views", session, userId] as const;
  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => {
      requireSessionVersion(session);
      return listSavedViews({ signal });
    },
    enabled: enabled && userId !== null,
    retry: false,
    staleTime: 30_000,
  });
  const mutation = useMutation({
    retry: false,
    scope: { id: JSON.stringify(queryKey) },
    mutationFn: async (command: SavedViewCommand & { session: number; userId: number | null }) => {
      requireSessionVersion(command.session);
      if (command.userId === null || command.userId !== getUser()?.id)
        throw new Error("saved_views_account_required");
      const key = ["saved-views", command.session, command.userId] as const;
      if (!client.getQueryData<SavedViewRead[]>(key)) throw new Error("saved_views_not_loaded");
      await client.cancelQueries({ queryKey: key, exact: true });
      requireSessionVersion(command.session);
      const updated =
        command.kind === "create"
          ? await createSavedView(command.name, command.filters)
          : command.kind === "update"
            ? await updateSavedView(command.id, command.payload)
            : await deleteSavedView(command.id);
      requireSessionVersion(command.session);
      await client.cancelQueries({ queryKey: key, exact: true });
      requireSessionVersion(command.session);
      client.setQueryData<SavedViewRead[]>(key, (current) => {
        if (!current) return current;
        if (command.kind === "delete") return current.filter((view) => view.id !== command.id);
        if (!updated) throw new Error("saved_view_acknowledgement_missing");
        if (command.kind === "update" && updated.id !== command.id)
          throw new Error("saved_view_identity_mismatch");
        return [...current.filter((view) => view.id !== updated.id), updated].sort((a, b) =>
          a.name.localeCompare(b.name),
        );
      });
      return updated;
    },
  });
  return {
    session,
    query,
    views: userId === null ? [] : (query.data ?? []),
    mutation: {
      ...mutation,
      mutate: (command: SavedViewCommand) => mutation.mutate({ ...command, session, userId }),
      mutateAsync: (command: SavedViewCommand) =>
        mutation.mutateAsync({ ...command, session, userId }),
    },
  };
}
