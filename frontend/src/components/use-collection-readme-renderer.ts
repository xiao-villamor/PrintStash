import { useQuery } from "@tanstack/react-query";

/** Share the on-demand renderer with the grid's readiness boundary: descriptions
 * and cards commit together, without a late renderer pushing the grid down. */
export function useCollectionReadmeRenderer(enabled: boolean) {
  return useQuery({
    queryKey: ["ui", "collection-markdown-renderer"],
    queryFn: () => import("@/components/markdown-view"),
    enabled,
    staleTime: Infinity,
    gcTime: Infinity,
  });
}
