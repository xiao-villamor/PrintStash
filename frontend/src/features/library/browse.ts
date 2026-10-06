import {
  infiniteQueryOptions,
  keepPreviousData,
  useInfiniteQuery,
  type InfiniteData,
} from "@tanstack/react-query";
import { listLibraryPage } from "@/lib/api/library-browse";
import { ApiError } from "@/lib/errors";
import { markStartup } from "@/lib/startup-timing";
import type { LibraryBrowsePage, LibraryBrowseParams } from "@/types/library-browse";

export const libraryBrowseKeys = {
  all: ["models", "browse"] as const,
  pages: (params: LibraryBrowseParams) => ["models", "browse", params] as const,
  authority: ["library-authority"] as const,
};

/** The server's sequence is authoritative; focus must not reorder a displayed list. */
export function libraryBrowseOptions(params: LibraryBrowseParams) {
  return infiniteQueryOptions<
    LibraryBrowsePage,
    Error,
    InfiniteData<LibraryBrowsePage>,
    ReturnType<typeof libraryBrowseKeys.pages>,
    string | null
  >({
    queryKey: libraryBrowseKeys.pages(params),
    initialPageParam: null,
    queryFn: ({ pageParam, signal }) => {
      markStartup("library-requests");
      return listLibraryPage({ ...params, cursor: pageParam ?? undefined }, { signal });
    },
    getNextPageParam: (lastPage) => lastPage.next_cursor,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: (failures, error) =>
      failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
  });
}

export function useLibraryBrowse(params: LibraryBrowseParams, enabled = true) {
  const query = useInfiniteQuery({
    ...libraryBrowseOptions(params),
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
