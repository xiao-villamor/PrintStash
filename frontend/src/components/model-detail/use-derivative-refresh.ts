"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { followModel } from "@/lib/events";
import { getSessionVersion } from "@/lib/session-transport";
import { modelDetailOptions } from "@/features/library/model-detail";
import type { DerivativeState } from "@/types";

const SETTLED: ReadonlySet<DerivativeState> = new Set(["ready", "skipped", "failed"]);

/**
 * Notices invalidate the one authorized read, coalescing while it is pending.
 * A subscription that starts from the revision already on screen invalidates nothing.
 */
export function useDerivativeRefresh(modelId: number): void {
  const client = useQueryClient();
  useEffect(() => {
    const session = getSessionVersion();
    return followModel(modelId, (notice) => {
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
      void client.invalidateQueries(
        { queryKey: modelDetailOptions(modelId).queryKey, exact: true },
        { cancelRefetch: false },
      );
    });
  }, [client, modelId]);
}
