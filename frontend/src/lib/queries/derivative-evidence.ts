import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { followModel } from "@/lib/events";
import { getSessionVersion } from "@/lib/session-transport";
import type { DerivativeKind } from "@/types";

export const derivativeEvidenceKey = (fileId: number | null, kind: DerivativeKind) =>
  ["derivative-evidence", fileId, kind] as const;

/** Replace pre-notice snapshots; coalesce changes arriving during the replacement read. */
export function useDerivativeEvidenceUpdates(
  modelId: number,
  fileId: number | null,
  kind: DerivativeKind,
) {
  const client = useQueryClient();
  useEffect(() => {
    if (fileId === null) return;
    const session = getSessionVersion();
    let active = true;
    let refreshing = false;
    let dirty = false;
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        while (active && dirty && session === getSessionVersion()) {
          dirty = false;
          // An initial request may hold a snapshot from before this notice.
          // Cancel it explicitly: Query's default refetch joins initial reads.
          await client.cancelQueries({
            queryKey: derivativeEvidenceKey(fileId, kind),
            exact: true,
          });
          if (active && session === getSessionVersion())
            await client.invalidateQueries({
              queryKey: derivativeEvidenceKey(fileId, kind),
              exact: true,
            });
        }
      } finally {
        refreshing = false;
      }
    }
    const stop = followModel(modelId, (notice) => {
      if (session !== getSessionVersion()) return;
      if (
        notice.type === "resync" ||
        notice.type === "derivative_policy" ||
        (notice.type === "derivative" && notice.file_id === fileId && notice.kind === kind)
      ) {
        dirty = true;
        void refresh();
      }
    });
    return () => {
      active = false;
      stop();
    };
  }, [client, modelId, fileId, kind]);
}
