import { useEffect, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { getCollectionPreview } from "@/lib/api/library-browse";
import { onAuthChange, retirePrivateSessionScope } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import { useLibraryStartup } from "@/lib/library-startup-context";
import type { CollectionRead } from "@/types";
import type {
  LibraryBrowseEntry,
  LibraryBrowsePage,
  LibraryBrowseParams,
  LibraryPreviewItem,
} from "@/types/library-browse";
import { narrowsLibrary } from "./filters";

const PREVIEW_LIMIT = 4;

export interface CollectionPreviewSource {
  page: LibraryBrowsePage;
  params: LibraryBrowseParams;
}

/** A prefix of the same unfiltered sequence can supply a descendant's first four
 * cards. Fewer matches are conclusive only after the source sequence ends. */
export function collectionPreviewItems(
  collection: CollectionRead,
  authorizationRevision: string | undefined,
  source: CollectionPreviewSource | undefined,
): LibraryBrowseEntry[] | null {
  if (
    collection.has_custom_cover ||
    !source ||
    source.page.authorization_revision !== authorizationRevision
  )
    return null;
  const {
    collection: parent,
    direct: requestedDirect,
    auto_scope: _autoScope,
    view,
    sort,
    limit: _limit,
    ...filters
  } = source.params;
  const direct = source.page.effective_direct ?? requestedDirect;
  if (direct || view !== "all" || sort !== "date-desc" || narrowsLibrary(filters)) return null;
  if (parent && collection.path !== parent && !collection.path.startsWith(`${parent}/`))
    return null;
  const items = source.page.items.filter((item) => {
    const path = item.kind === "model" ? item.model.collection : item.multipart.collection;
    return path === collection.path || path?.startsWith(`${collection.path}/`);
  });
  return items.length >= PREVIEW_LIMIT || source.page.next_cursor === null
    ? items.slice(0, PREVIEW_LIMIT)
    : null;
}

function previewItem(item: LibraryBrowseEntry): LibraryPreviewItem {
  return item.kind === "model"
    ? { kind: item.kind, id: item.model.id, thumbnail_url: item.model.thumbnail_url }
    : { kind: item.kind, id: item.multipart.id, thumbnail_url: item.multipart.cover_thumbnail_url };
}

/** A bounded descendant read, admitted near the viewport after the page's list
 * arrives and its first Model row settles. Reusing an authorized prefix already
 * in memory needs no competing metadata request. */
export function useCollectionOverview(
  collection: CollectionRead,
  authorizationRevision: string | undefined,
  onAuthorityRetired: () => void,
  afterPrimary = true,
  source?: CollectionPreviewSource,
) {
  const { ref, admitted } = useViewportAdmission();
  const startup = useLibraryStartup();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const active = session === currentSession;
  // Admission gates network work, not the authorized snapshot already on screen.
  const reused = collectionPreviewItems(collection, authorizationRevision, source);
  const query = useQuery({
    queryKey: [
      ...queryKeys.models,
      "collection-overview",
      {
        session,
        id: collection.id,
        path: collection.path,
        authorizationRevision,
        limit: PREVIEW_LIMIT,
      },
    ],
    enabled:
      active &&
      admitted &&
      afterPrimary &&
      startup.canLoad("collection-previews") &&
      reused === null &&
      !!authorizationRevision &&
      (collection.model_count > 0 || !!collection.has_custom_cover),
    queryFn: async ({ signal }) => {
      requireSessionVersion(session);
      const page = await getCollectionPreview(collection.path, { signal });
      requireSessionVersion(session);
      return page;
    },
    retry: (failures, error) =>
      failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
  });
  const mismatch = Boolean(
    reused === null &&
    authorizationRevision &&
    query.data &&
    query.data.authorization_revision !== authorizationRevision,
  );
  const refused =
    reused === null &&
    query.error instanceof ApiError &&
    (query.error.status === 401 || query.error.status === 403);
  const retired = mismatch || refused;
  useEffect(() => {
    if (active && retired && getSessionVersion() === session) {
      retirePrivateSessionScope();
      onAuthorityRetired();
    }
  }, [active, retired, onAuthorityRetired, session]);
  // Refetch errors and authority mismatches suppress cached private imagery in render.
  const visible =
    active && !!authorizationRevision && (reused !== null || (!query.isError && !mismatch));
  return {
    ref,
    pending:
      active &&
      admitted &&
      reused === null &&
      (collection.model_count > 0 || !!collection.has_custom_cover) &&
      (!authorizationRevision || query.isPending),
    image: visible && reused === null ? query.data?.image_url : undefined,
    models: visible
      ? collection.model_count === 0 && !collection.has_custom_cover
        ? []
        : (reused?.map(previewItem) ?? query.data?.items)?.map((item) => ({
            key: `${item.kind}:${item.id}`,
            thumbnail: item.thumbnail_url,
          }))
      : undefined,
    error: active && reused === null && query.isError,
    retry: () => {
      if (active && getSessionVersion() === session) void query.refetch();
    },
  };
}
