import type { QueryClient } from "@tanstack/react-query";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";

/** Refresh derived metadata without replacing controlled browse/history snapshots. */
export function refreshLibraryMetadata(
  client: QueryClient,
  session: number,
  acknowledged?: { kind: "model" | "multipart"; id: number },
): void {
  if (session !== getSessionVersion()) return;
  const preserved = acknowledged
    ? acknowledged.kind === "model"
      ? queryKeys.model(acknowledged.id)
      : queryKeys.multipartModel(acknowledged.id)
    : null;
  for (const queryKey of [
    queryKeys.models,
    queryKeys.multipartModels,
    queryKeys.collections,
    queryKeys.tags,
    queryKeys.vaultStats,
  ]) {
    void client.invalidateQueries({
      queryKey,
      predicate: (query) =>
        !(
          preserved &&
          query.queryKey.length === 2 &&
          query.queryKey[0] === preserved[0] &&
          query.queryKey[1] === preserved[1]
        ),
    });
  }
}
