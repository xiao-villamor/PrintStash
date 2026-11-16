import { uiText } from "@/lib/locale";

/** Each task produces one cover image or one multi-angle set for an Artifact. */
export interface PreviewActivity {
  total: number;
  completed: number;
  remaining: number;
  skipped: number;
  failed: number;
}

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
