import { useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { useViewportAdmission } from "@/lib/use-viewport-admission";
import type { ModelBoundingBoxRead, ModelRead } from "@/types";
import { formatNumber } from "@/lib/format";
import { normalizeRecommendedGcodeFiles } from "@/components/model-detail/presentation";
import { modelDetailOptions } from "./model-detail";

/** The list projection omits revision IDs and geometry. Read the existing detail
 * only for admitted cards, sharing the inspector's cache and cancellation. */
export function useCardDetail(id: number, enabled: boolean) {
  const { ref, admitted } = useViewportAdmission();
  const [session] = useState(getSessionVersion);
  const currentSession = useSyncExternalStore(onAuthChange, getSessionVersion);
  const active = session === currentSession;
  const query = useQuery({
    ...modelDetailOptions(id),
    enabled: enabled && admitted && active,
  });
  return { ref, admitted: admitted && active, data: active ? query.data : undefined };
}

export function cardRevisions(model: ModelRead | undefined) {
  return normalizeRecommendedGcodeFiles(model?.files ?? [])
    .filter((file) => file.file_type === "gcode")
    .sort((a, b) => b.version - a.version);
}

/** The card's size line, from the list row: no Model detail read is needed. */
export function cardDimensions(box: ModelBoundingBoxRead | null): string | null {
  if (!box) return null;
  return (
    [box.x_mm, box.y_mm, box.z_mm]
      .map((value) => formatNumber(value, { maximumFractionDigits: 1 }))
      .join(" × ") + " mm"
  );
}
