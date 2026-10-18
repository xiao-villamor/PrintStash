"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { followModel } from "@/lib/events";
import { getSessionVersion } from "@/lib/session-transport";
import { modelDetailOptions } from "@/features/library/model-detail";
import type { DerivativeState } from "@/types";

const SETTLED: ReadonlySet<DerivativeState> = new Set(["ready", "skipped", "failed"]);

/**
 * Notices invalidate the authorized read; hints received during it coalesce
 * into one trailing read so a response captured earlier cannot remain current.
 * A subscription that starts from the revision already on screen invalidates nothing.
 */
export function useDerivativeRefresh(modelId: number): void {
  const client = useQueryClient();
  useEffect(() => {
    const session = getSessionVersion();
    const queryKey = modelDetailOptions(modelId).queryKey;
    let active = true;
    let refreshing = false;
    let needsRead = false;
    async function refresh() {
      if (refreshing) return;
      refreshing = true;
      try {
        while (active && session === getSessionVersion() && needsRead) {
          needsRead = false;
          // Joining another owner's request can return a snapshot from before
          // this hint. It needs a fresh read even when no second hint arrives.
          const joinedRead = client.getQueryState(queryKey)?.fetchStatus === "fetching";
          await client.invalidateQueries({ queryKey, exact: true }, { cancelRefetch: false });
          if (joinedRead) needsRead = true;
        }
      } finally {
        refreshing = false;
      }
    }
    const stop = followModel(modelId, (notice) => {
      if (session !== getSessionVersion()) return;
      if (notice.type === "derivative" && !SETTLED.has(notice.state)) return;
      // The acknowledgement names the derivative revision the subscription
      // starts from. A detail already at that revision missed nothing, so the
      // read that would only confirm it is skipped.
      if (
        notice.type === "subscribed" &&
        notice.revision !== undefined &&
        client.getQueryData(modelDetailOptions(modelId).queryKey)?.derivatives_revision ===
          notice.revision
      )
        return;
      needsRead = true;
      void refresh();
    });
    return () => {
      active = false;
      stop();
    };
  }, [client, modelId]);
}
