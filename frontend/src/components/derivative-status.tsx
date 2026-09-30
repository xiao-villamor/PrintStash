"use client";

/**
 * What is still being derived from one Artifact, and what failed.
 *
 * A fresh upload's preview and metadata are produced in the background, so an
 * Artifact row says so rather than looking broken; a failure names its reason
 * and, for an editor, offers a retry. Everything finished renders nothing.
 * Missing derivatives are unknown, never zero, which is why a pending kind is
 * shown as pending rather than hidden.
 */
import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Loader2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { listDerivatives, retryDerivative } from "@/lib/api";
import { getErrorMessage } from "@/lib/errors";
import { followModel } from "@/lib/events";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
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
  api = DERIVATIVE_API,
}: {
  modelId: number;
  fileId: number;
  canRetry: boolean;
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
  const disabled = derivatives.some((item) => item.state === "disabled");
  if (preparing.length === 0 && failed.length === 0 && !disabled) return null;

  return (
    <ul className="mt-1.5 space-y-1">
      {disabled && (
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
      {failed.map((item) => (
        <li key={item.kind} className="flex flex-wrap items-center gap-1.5 text-2xs">
          <AlertTriangle className="h-3 w-3 text-warning" aria-hidden />
          <span>
            {uiText("{kind} failed: {reason}", {
              kind: kindLabel(item.kind),
              reason: getErrorMessage(item.failure_reason ?? "unknown"),
            })}
          </span>
          {canRetry && item.retryable && (
            <Button
              size="sm"
              variant="ghost"
              loading={retrying === item.kind}
              onClick={() => void retry(item.kind)}
            >
              {uiText("Retry {kind}", { kind: kindLabel(item.kind) })}
            </Button>
          )}
        </li>
      ))}
    </ul>
  );
}
