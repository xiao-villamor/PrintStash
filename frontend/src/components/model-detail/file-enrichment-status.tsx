"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { getModel, requestFileEnrichment } from "@/lib/api";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";
import type { FileRead, ModelRead } from "@/types";

export function FileEnrichmentStatus({
  file,
  modelId,
  canEdit,
  onModel,
}: {
  file: FileRead;
  modelId: number;
  canEdit: boolean;
  onModel: (model: ModelRead) => void;
}) {
  useUiLocale();
  const [requesting, setRequesting] = useState(false);
  const states = [file.enrichment?.metadata, file.enrichment?.thumbnail];
  const pending = states.some(
    (state) => state === "pending" || state === "running" || state === "blocked",
  );
  const failed = states.includes("failed");
  const optional = states.includes("on_demand") || states.includes("disabled");

  async function request() {
    setRequesting(true);
    try {
      await requestFileEnrichment(file.id);
      onModel(await getModel(modelId, { fresh: true }));
    } catch (error) {
      toast.error(error);
    } finally {
      setRequesting(false);
    }
  }

  if (!pending && !failed && !optional) return null;
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      <span>
        {uiText(
          pending
            ? "Preparing previews and details"
            : failed
              ? "Some details could not be prepared"
              : "Preview available on request",
        )}
      </span>
      {canEdit && !pending && (
        <Button variant="ghost" size="sm" disabled={requesting} onClick={() => void request()}>
          {uiText(failed ? "Retry enrichment" : "Generate preview")}
        </Button>
      )}
    </div>
  );
}
