import { useQueryClient } from "@tanstack/react-query";
import * as api from "@/lib/api/taxonomy";
import { queryKeys } from "@/lib/query-client";
import { withSessionRequest } from "@/lib/session-transport";

/** Taxonomy writes affect choices, counts and labels; browse snapshots refresh deliberately. */
export function useTaxonomyCommands(overrides?: Pick<typeof api, "createCollection">) {
  const client = useQueryClient();
  async function change<T>(kind: "tags" | "collections", write: () => Promise<T>): Promise<T> {
    return withSessionRequest(async (request) => {
      const value = await write();
      request.assertCurrent();
      const keys = [
        queryKeys.models,
        queryKeys.collections,
        queryKeys.multipartModels,
        queryKeys.vaultStats,
        ...(kind === "tags" ? [queryKeys.tags] : []),
      ];
      await Promise.all(keys.map((queryKey) => client.cancelQueries({ queryKey })));
      request.assertCurrent();
      for (const queryKey of keys) void client.invalidateQueries({ queryKey });
      void client.resetQueries({ queryKey: queryKeys.outliner });
      return value;
    });
  }
  return {
    createTag: (...args: Parameters<typeof api.createTag>) =>
      change("tags", () => api.createTag(...args)),
    deleteTag: (...args: Parameters<typeof api.deleteTag>) =>
      change("tags", () => api.deleteTag(...args)),
    createCollection: (...args: Parameters<typeof api.createCollection>) =>
      change("collections", () => (overrides?.createCollection ?? api.createCollection)(...args)),
    deleteCollection: (...args: Parameters<typeof api.deleteCollection>) =>
      change("collections", () => api.deleteCollection(...args)),
    moveCollection: (...args: Parameters<typeof api.moveCollection>) =>
      change("collections", () => api.moveCollection(...args)),
    renameCollection: (...args: Parameters<typeof api.renameCollection>) =>
      change("collections", () => api.renameCollection(...args)),
    replaceCollectionTags: (...args: Parameters<typeof api.replaceCollectionTags>) =>
      change("collections", () => api.replaceCollectionTags(...args)),
    setCollectionReadme: (...args: Parameters<typeof api.setCollectionReadme>) =>
      change("collections", () => api.setCollectionReadme(...args)),
  };
}
