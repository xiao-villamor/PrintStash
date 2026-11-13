import { useEffect, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { listLibraryPage } from "@/lib/api/library-browse";
import { onAuthChange, retirePrivateSessionScope } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import type { CollectionRead } from "@/types";

const PREVIEW_LIMIT = 4;

/** A bounded descendant read, admitted only when the collection card approaches view. */
export function useCollectionOverview(
  collection: CollectionRead,
  authorizationRevision: string | undefined,
  onAuthorityRetired: () => void,
) {
  const { ref, admitted } = useViewportAdmission();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const active = session === currentSession;
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
    enabled: active && admitted && !!authorizationRevision && collection.model_count > 0,
    queryFn: async ({ signal }) => {
      requireSessionVersion(session);
      const page = await listLibraryPage(
        {
          collection: collection.path,
          direct: false,
          limit: PREVIEW_LIMIT,
          sort: "date-desc",
          view: "all",
        },
        { signal },
      );
      requireSessionVersion(session);
      return page;
    },
    retry: (failures, error) =>
      failures < 1 && !(error instanceof ApiError && error.status >= 400 && error.status < 500),
  });
  const mismatch = Boolean(
    authorizationRevision &&
    query.data &&
    query.data.authorization_revision !== authorizationRevision,
  );
  const refused =
    query.error instanceof ApiError && (query.error.status === 401 || query.error.status === 403);
  const retired = mismatch || refused;
  useEffect(() => {
    if (active && retired && getSessionVersion() === session) {
      retirePrivateSessionScope();
      onAuthorityRetired();
    }
  }, [active, retired, onAuthorityRetired, session]);
  // Refetch errors and authority mismatches suppress cached private imagery in render.
  const visible = active && !!authorizationRevision && !query.isError && !mismatch;
  return {
    ref,
    pending:
      active &&
      admitted &&
      collection.model_count > 0 &&
      (!authorizationRevision || query.isPending),
    models: visible
      ? collection.model_count === 0
        ? []
        : query.data?.items.map((item) =>
            item.kind === "model"
              ? { key: `model:${item.model.id}`, thumbnail: item.model.thumbnail_url }
              : {
                  key: `multipart:${item.multipart.id}`,
                  thumbnail: item.multipart.cover_thumbnail_url,
                },
          )
      : undefined,
    error: active && query.isError,
    retry: () => {
      if (active && getSessionVersion() === session) void query.refetch();
    },
  };
}
