"use client";

import { useEffect } from "react";
import { queryOptions, skipToken, useQuery, useQueryClient } from "@tanstack/react-query";
import { getFilePlates, getGcodePlates } from "@/lib/api/plates";
import { followModel } from "@/lib/events";
import { getSessionVersion } from "@/lib/session-transport";
import type { DerivativeState, FilePlates } from "@/types";

const SETTLED: ReadonlySet<DerivativeState> = new Set(["ready", "skipped", "failed"]);

export const filePlatesKeys = {
  all: ["file-plates"] as const,
  file: (fileId: number | null) => ["file-plates", fileId] as const,
};

/** ``null`` reads nothing: the model has no 3MF to lay out. */
export function filePlatesOptions(fileId: number | null) {
  return queryOptions({
    queryKey: filePlatesKeys.file(fileId),
    queryFn: fileId === null ? skipToken : ({ signal }) => getFilePlates(fileId, signal),
  });
}

/**
 * A 3MF's plates, read again when its `plates` derivative settles. ``null``
 * while there is no 3MF to read or its layout is not known yet.
 */
export function useFilePlates(modelId: number, fileId: number | null): FilePlates | null {
  const client = useQueryClient();
  const query = useQuery(filePlatesOptions(fileId));
  useEffect(() => {
    if (fileId === null) return;
    const session = getSessionVersion();
    return followModel(modelId, (notice) => {
      if (session !== getSessionVersion()) return;
      if (
        notice.type === "resync" ||
        (notice.type === "derivative" &&
          notice.kind === "plates" &&
          notice.file_id === fileId &&
          SETTLED.has(notice.state))
      )
        void client.invalidateQueries({ queryKey: filePlatesKeys.file(fileId), exact: true });
    });
  }, [client, modelId, fileId]);
  return fileId === null ? null : (query.data ?? null);
}

/** Only read archive metadata when a 3MF is selected for toolpath viewing. */
export function gcodePlatesOptions(fileId: number | null) {
  return queryOptions({
    queryKey: ["gcode-plates", fileId] as const,
    queryFn: fileId === null ? skipToken : ({ signal }) => getGcodePlates(fileId, signal),
  });
}
