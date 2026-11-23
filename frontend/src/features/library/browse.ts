import {
  infiniteQueryOptions,
  keepPreviousData,
  useInfiniteQuery,
  type InfiniteData,
} from "@tanstack/react-query";
import { listLibraryPage } from "@/lib/api/library-browse";
import { ApiError } from "@/lib/errors";
import { markStartup } from "@/lib/startup-timing";
import { acquireAssetUrl } from "@/lib/asset-cache";
import type { LibraryBrowsePage, LibraryBrowseParams } from "@/types/library-browse";

export const libraryBrowseKeys = {
  all: ["models", "browse"] as const,
  pages: (params: LibraryBrowseParams) => ["models", "browse", params] as const,
  authority: ["library-authority"] as const,
};

/** The server's sequence is authoritative; focus must not reorder a displayed list. */
export function libraryBrowseOptions(params: LibraryBrowseParams, firstRowSize = 0) {
  return infiniteQueryOptions<
    LibraryBrowsePage,
    Error,
    InfiniteData<LibraryBrowsePage>,
    ReturnType<typeof libraryBrowseKeys.pages>,
    string | null
  >({
    queryKey: libraryBrowseKeys.pages(params),
    initialPageParam: null,
    queryFn: async ({ pageParam, signal }) => {
      markStartup("library-requests");
      let page: LibraryBrowsePage;
      try {
        page = await listLibraryPage({ ...params, cursor: pageParam ?? undefined }, { signal });
      } catch (error) {
        // No cursor has been accepted for a first page. One fresh read can
        // recover a concurrent writer without changing any displayed sequence.
        // A continuation conflict belongs to the coordinated refresh flow.
        if (
          pageParam !== null ||
          !(error instanceof ApiError) ||
          error.status !== 409 ||
          error.code !== "browse_refresh_required"
        )
          throw error;
        signal.throwIfAborted();
        page = await listLibraryPage(params, { signal });
      }
      // The active grid already measured its first row. Start those authorized
      // bytes before React renders the cards and waits for intersection delivery.
      // Display consumers share the same leases; metadata-only prefetches and
      // continuation pages retain viewport admission.
      signal.throwIfAborted();
      if (pageParam === null && firstRowSize > 0) {
        for (const item of page.items.slice(0, firstRowSize)) {
          const path =
            item.kind === "model" ? item.model.thumbnail_url : item.multipart.cover_thumbnail_url;
          if (!path || path.startsWith("http://") || path.startsWith("https://")) continue;
          const lease = acquireAssetUrl(path, signal);
          void lease.url.then(
            () => lease.release(),
            () => lease.release(),
          );
        }
      }
      return page;
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: (failures, error) =>
      failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
  });
}

export function useLibraryBrowse(params: LibraryBrowseParams, enabled = true, firstRowSize = 0) {
  const query = useInfiniteQuery({
    ...libraryBrowseOptions(params, firstRowSize),
    enabled,
    placeholderData: keepPreviousData,
  });
  const refreshRequired =
    query.error instanceof ApiError && query.error.code === "browse_refresh_required";
  const loadMore = () => {
    if (!query.hasNextPage || query.isFetching || query.isPlaceholderData || refreshRequired)
      return;
    return query.fetchNextPage({ cancelRefetch: false });
  };
  return { ...query, refreshRequired, loadMore };
}
