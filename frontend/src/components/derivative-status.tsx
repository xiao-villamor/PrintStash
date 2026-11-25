"use client";

import { Link } from "@/lib/link";

/**
 * What is still being derived from one Artifact, and what failed.
 *
 * A fresh upload's preview and metadata are produced in the background, so an
 * Artifact row says so rather than looking broken; a failure names its reason
 * and, for an editor, offers a retry. Failures that share a cause share one
 * explanation, phrased as what the reader can do rather than as a code.
 * Everything finished renders nothing.
 * Missing derivatives are unknown, never zero, which is why a pending kind is
 * shown as pending rather than hidden.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ChevronRight, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { listDerivatives, retryDerivative } from "@/lib/api";
import {
  derivativeFailureCause,
  derivativeFailureMessage,
  type DerivativeFailureCause,
} from "@/lib/derivative-failures";
import { followModel } from "@/lib/events";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { stlPreviewMessage, type StlPreviewFailure } from "@/lib/use-stl-preview";
import { toast } from "@/lib/toast";
import { cn } from "@/lib/utils";
import type { DerivativeRead, DerivativeState } from "@/types";

export interface DerivativeStatusApi {
  list: (fileId: number) => Promise<DerivativeRead[]>;
  retry: (fileId: number, kind: string) => Promise<DerivativeRead[]>;
}

const DERIVATIVE_API: DerivativeStatusApi = { list: listDerivatives, retry: retryDerivative };

const IN_PROGRESS: ReadonlySet<DerivativeState> = new Set(["pending", "queued", "running"]);

function kindLabel(kind: string): string {
  switch (kind) {
    case "thumbnail":
      return uiText("Preview");
    case "metadata":
      return uiText("Metadata");
    case "viewer_stl":
      return uiText("3D preview");
    case "project_profile":
      return uiText("3MF project settings");
    case "plates":
      return uiText("Plates");
    case "toolpath":
      return uiText("Toolpath");
    default:
      return kind;
  }
}

export function DerivativeStatus({
  modelId,
  fileId,
  canRetry,
  previewFailure = null,
  showReady = false,
  plateCount = null,
  className,
  api = DERIVATIVE_API,
}: {
  modelId: number;
  fileId: number;
  canRetry: boolean;
  previewFailure?: StlPreviewFailure | null;
  /** Show the settled preview state as a processing row, not only work in flight. */
  showReady?: boolean;
  /** Plates detected in the file, when the page already knows them. */
  plateCount?: number | null;
  className?: string;
  api?: DerivativeStatusApi;
}) {
  useUiLocale();
  const [derivatives, setDerivatives] = useState<DerivativeRead[]>([]);
  const [retrying, setRetrying] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .list(fileId)
      .then(setDerivatives)
      .catch(() => {
        // Status is advisory; the Artifact itself is still usable.
      });
  }, [api, fileId]);

  useEffect(() => {
    refresh();
    return followModel(modelId, (notice) => {
      if (
        notice.type === "resync" ||
        notice.type === "derivative_policy" ||
        (notice.type === "derivative" && notice.file_id === fileId)
      )
        refresh();
    });
  }, [modelId, fileId, refresh]);

  async function retry(kind: string) {
    setRetrying(kind);
    try {
      setDerivatives(await api.retry(fileId, kind));
    } catch (error) {
      toast.error(error);
    } finally {
      setRetrying(null);
    }
  }

  const preparing = derivatives.filter((item) => IN_PROGRESS.has(item.state));
  const failed = derivatives.filter((item) => item.state === "failed");
  // Outputs that failed for the same cause share one explanation.
  const failures = new Map<DerivativeFailureCause, DerivativeRead[]>();
  for (const item of failed) {
    const cause = derivativeFailureCause(item.failure_reason);
    failures.set(cause, [...(failures.get(cause) ?? []), item]);
  }
  const disabled = derivatives.some((item) => item.state === "disabled");
  const previewJobs = derivatives.filter(
    (item) => item.kind === "viewer_stl" || item.kind === "previews" || item.kind === "plates",
  );
  const previewDisabled = previewJobs.some((item) => item.state === "disabled");
  const ready = showReady && previewJobs.some((item) => item.state === "ready");
  const previewMessage = previewFailure === null ? null : stlPreviewMessage(previewFailure);
  const showPreviewFailure =
    previewMessage !== null && ![...failures.keys()].some((cause) => cause === previewFailure);
  if (preparing.length === 0 && failed.length === 0 && !disabled && !showPreviewFailure && !ready)
    return null;

  return (
    <ul className={cn("min-w-0 space-y-2", className)}>
      {showPreviewFailure && (
        <li role="status" className="flex items-start gap-1.5 text-2xs">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" aria-hidden />
          <div className="min-w-0 flex-1 space-y-1 break-words">
            <p className="font-medium text-on-surface">{uiText("Failed to load 3D preview")}</p>
            <p className="text-xs leading-relaxed text-on-surface-variant">{previewMessage}</p>
          </div>
        </li>
      )}
      {ready && !previewDisabled && (
        <li className="flex items-start gap-2.5 text-sm">
          <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full bg-success" />
          <span className="min-w-0 flex-1 leading-5">
            {uiText("Preview and plates ready")}
            <span className="block text-muted-foreground">
              {plateCount !== null && plateCount > 1
                ? uiText("{count} plates detected · thumbnails and dimensions available", {
                    count: plateCount,
                  })
                : uiText("Thumbnail and dimensions available")}
            </span>
          </span>
        </li>
      )}
      {showReady && previewDisabled && (
        <li role="status" className="flex items-start gap-2.5 text-sm">
          <span aria-hidden className="mt-1.5 size-2 shrink-0 rounded-full bg-muted-foreground" />
          <span className="min-w-0 flex-1 leading-5">
            {uiText("3D preview processing is disabled")}
            <span className="block text-muted-foreground">
              {uiText(
                "No server thumbnail, plate detection or dimensions for this file. The browser preview still works.",
              )}
            </span>
          </span>
          {canRetry && (
            <Link
              href="/settings?section=work"
              className="inline-flex h-8 shrink-0 items-center gap-1 px-2 text-sm text-foreground hover:underline"
            >
              {uiText("Processing settings")}
              <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden />
            </Link>
          )}
        </li>
      )}
      {disabled && !(showReady && previewDisabled) && (
        <li role="status" className="text-2xs text-on-surface-variant">
          {uiText("Processing disabled")}
        </li>
      )}
      {preparing.length > 0 && (
        <li role="status" className="flex items-center gap-1.5 text-2xs text-on-surface-variant">
          <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
          {uiText("Preparing {kinds}…", {
            kinds: preparing.map((item) => kindLabel(item.kind)).join(", "),
          })}
        </li>
      )}
      {[...failures].map(([cause, items]) => (
        <li key={cause} className="flex items-start gap-1.5 text-2xs">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" aria-hidden />
          <div className="min-w-0 flex-1 space-y-1 break-words">
            <p className="font-medium text-on-surface">
              {uiText("{kinds} failed", {
                kinds: items.map((item) => kindLabel(item.kind)).join(", "),
              })}
            </p>
            <p className="text-xs leading-relaxed text-on-surface-variant">
              {derivativeFailureMessage(cause)}
            </p>
            {canRetry && items.some((item) => item.retryable) && (
              <div className="flex flex-wrap gap-1">
                {items
                  .filter((item) => item.retryable)
                  .map((item) => (
                    <Button
                      key={item.kind}
                      size="sm"
                      variant="outline"
                      className="h-auto min-h-11 max-w-full whitespace-normal text-left sm:min-h-8"
                      loading={retrying === item.kind}
                      onClick={() => void retry(item.kind)}
                    >
                      {uiText("Retry {kind}", { kind: kindLabel(item.kind) })}
                    </Button>
                  ))}
              </div>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}
