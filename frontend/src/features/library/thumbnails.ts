import { useEffect, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { getLibraryThumbnails } from "@/lib/api/library-browse";
import { ApiError } from "@/lib/errors";
import { onAuthChange, retirePrivateSessionScope } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { MAX_FOLLOWED } from "@/lib/use-thumbnail-arrivals";
import type { LibraryBrowsePage, LibraryThumbnailProjection } from "@/types/library-browse";
import { libraryBrowseKeys } from "./browse";

function decodeProjection(wire: LibraryThumbnailProjection): LibraryThumbnailProjection {
  // oxlint-disable anti-slop/no-runtime-typeof -- Foreign HTTP JSON must establish its authority token and nullable URL fields before it can retire private scope or publish assets.
  if (
    typeof wire?.authorization_revision !== "string" ||
    !wire.authorization_revision.length ||
    !Array.isArray(wire.items) ||
    !wire.items.every(
      (item) =>
        Number.isSafeInteger(item?.model_id) &&
        item.model_id > 0 &&
        (item.thumbnail_url === null || typeof item.thumbnail_url === "string"),
    )
  )
    throw new Error("invalid_thumbnail_projection");
  // oxlint-enable anti-slop/no-runtime-typeof
  return wire;
}

/** A derivative arrival may fill a placeholder, never replace a browse snapshot. */
export function useLibraryThumbnails(
  models: ReadonlyArray<{ id: number; thumbnail_url: string | null }>,
  presentation: LibraryBrowsePage | null,
  onAuthorityRetired: () => void,
) {
  const client = useQueryClient();
  const [mountedSession] = useState(getSessionVersion);
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const ids = [
    ...new Set(models.filter((model) => model.thumbnail_url === null).map((model) => model.id)),
  ].slice(0, MAX_FOLLOWED);
  const authorization = presentation?.authorization_revision;
  const query = useQuery({
    queryKey: ["models", "thumbnail-projection", authorization, ids],
    enabled: false,
    retry: false,
    queryFn: async ({ signal }) => {
      requireSessionVersion(mountedSession);
      return decodeProjection(await getLibraryThumbnails(ids, { signal }));
    },
  });
  const projection = query.data;
  const mismatch = Boolean(
    authorization && projection && projection.authorization_revision !== authorization,
  );
  const refused =
    query.error instanceof ApiError &&
    (query.error.status === 401 ||
      query.error.status === 403 ||
      (query.error.status === 409 && query.error.code === "browse_refresh_required"));
  const authorizationChanged = mismatch || refused || session !== mountedSession;
  useEffect(() => {
    if (session !== mountedSession) return;
    if (mismatch || refused) {
      retirePrivateSessionScope();
      onAuthorityRetired();
      return;
    }
    if (!projection || !authorization) return;
    const thumbnails = new Map(projection.items.map((item) => [item.model_id, item.thumbnail_url]));
    client.setQueriesData<InfiniteData<LibraryBrowsePage>>(
      { queryKey: libraryBrowseKeys.all },
      (data) => {
        if (!data) return data;
        let changed = false;
        const pages = data.pages.map((page) => {
          if (page.authorization_revision !== authorization) return page;
          const items = page.items.map((item) => {
            if (item.kind !== "model" || item.model.thumbnail_url !== null) return item;
            const url = thumbnails.get(item.model.id);
            if (!url) return item;
            changed = true;
            return { ...item, model: { ...item.model, thumbnail_url: url } };
          });
          return { ...page, items };
        });
        return changed ? { ...data, pages } : data;
      },
    );
  }, [
    authorization,
    client,
    mountedSession,
    onAuthorityRetired,
    projection,
    session,
    mismatch,
    refused,
  ]);
  return {
    authorizationChanged,
    error: query.error,
    refresh: () => {
      if (
        authorization &&
        ids.length &&
        !authorizationChanged &&
        getSessionVersion() === mountedSession
      )
        return query.refetch({ cancelRefetch: false });
    },
  };
}
