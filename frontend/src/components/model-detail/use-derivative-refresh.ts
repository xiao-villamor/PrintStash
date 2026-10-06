"use client";

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { followModel } from "@/lib/events";
import { getSessionVersion } from "@/lib/session-transport";
import { modelDetailOptions } from "@/features/library/model-detail";
import type { DerivativeState } from "@/types";

const SETTLED: ReadonlySet<DerivativeState> = new Set(["ready", "skipped", "failed"]);

/** Notices invalidate the one authorized read, coalescing while it is pending. */
export function useDerivativeRefresh(modelId: number): void {
  const client = useQueryClient();
  useEffect(() => {
    const session = getSessionVersion();
    return followModel(modelId, (notice) => {
      if (session !== getSessionVersion()) return;
      if (notice.type === "derivative" && !SETTLED.has(notice.state)) return;
      void client.invalidateQueries(
        { queryKey: modelDetailOptions(modelId).queryKey, exact: true },
        { cancelRefetch: false },
      );
    });
  }, [client, modelId]);
}
