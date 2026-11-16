import { uiText } from "@/lib/locale";

import type { PreviewActivity } from "@/types";

export function previewProgress(activity: PreviewActivity) {
  const processed = activity.total - activity.remaining;
  return {
    total: activity.total,
    processed,
    succeeded: activity.completed,
    failed: activity.failed,
    skipped: activity.skipped,
    progress: activity.total === 0 ? 100 : (processed / activity.total) * 100,
    detail: uiText(
      "{total} preview tasks · {completed} done · {remaining} remaining · {skipped} skipped · {failed} failed",
      { ...activity },
    ),
  };
}
