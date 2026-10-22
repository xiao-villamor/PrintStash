/**
 * Settings → Background work → Worker settings: how many model files the
 * mesh pipeline processes at once. Automatic follows the machine's CPUs and
 * memory; an administrator may choose a count up to what memory fits.
 */
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { useMeshWorkers } from "@/features/work/queries";
import { meshWorkersApi, type MeshWorkersRead } from "@/lib/api/mesh-workers";
import { parseApiError } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { toast } from "@/lib/toast";

function sourceLabel(value: MeshWorkersRead): string {
  switch (value.source) {
    case "database":
      return uiText("Chosen by an administrator");
    case "environment":
      return uiText("Set by VAULT_MAX_RENDER_JOBS");
    case "automatic":
      return uiText("Automatic");
  }
}

export function MeshWorkersSection({ api = meshWorkersApi }: { api?: typeof meshWorkersApi }) {
  const { query, mutation } = useMeshWorkers(api);
  const [draft, setDraft] = useState<string | null>(null);
  const value = query.data;
  if (query.isError)
    return (
      <p className="border-b px-4 py-3 text-xs text-muted-foreground sm:px-5">
        {uiText("The mesh job count could not be loaded.")}
      </p>
    );
  if (!value)
    return (
      <div className="border-b px-4 py-3 sm:px-5">
        <Skeleton className="h-9 w-full" />
      </div>
    );
  const shown = draft ?? String(value.jobs);
  const parsed = Number(shown);
  const valid = Number.isInteger(parsed) && parsed >= 1 && parsed <= value.maximum;
  const busy = mutation.isPending;

  async function change(jobs: number | null) {
    if (!value) return;
    try {
      const saved = await mutation.mutateAsync(
        jobs === null ? { kind: "reset", base: value } : { kind: "save", jobs, base: value },
      );
      setDraft(null);
      toast.success(uiText("Processing {count} models at once", { count: saved.jobs }));
    } catch (cause) {
      if (parseApiError(cause).status === 412) {
        setDraft(null);
        void query.refetch();
        toast.error(
          uiText(
            "Another administrator changed this setting. Review the current value and try again.",
          ),
        );
      } else toast.error(cause);
    }
  }

  return (
    <div className="flex flex-col gap-3 border-b px-4 py-3 sm:flex-row sm:items-center sm:px-5">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{uiText("Models processed at once")}</span>
          <Badge variant={value.source === "automatic" ? "outline" : "secondary"}>
            {sourceLabel(value)}
          </Badge>
        </div>
        <p className="text-xs text-muted-foreground">
          {uiText(
            "Automatic picks {automatic} for this machine's CPUs and memory; up to {maximum} fit its memory.",
            { automatic: value.automatic, maximum: value.maximum },
          )}
        </p>
        {value.restart_required && (
          <p className="text-xs text-muted-foreground">
            {uiText("Separate worker processes apply a change when they restart.")}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Input
          type="number"
          min={1}
          max={value.maximum}
          className="w-20"
          value={shown}
          aria-label={uiText("Models processed at once")}
          onChange={(event) => setDraft(event.target.value)}
        />
        <Button
          size="sm"
          variant="outline"
          disabled={!valid || parsed === value.jobs || busy}
          aria-label={uiText("Save models processed at once")}
          onClick={() => void change(parsed)}
        >
          {uiText("Save")}
        </Button>
        {value.source === "database" && (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void change(null)}>
            {uiText("Use default")}
          </Button>
        )}
      </div>
    </div>
  );
}
