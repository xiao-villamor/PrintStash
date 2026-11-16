/** One toolpath at a time, from an ordinary Revision or a sliced 3MF plate. */
import { lazy, Suspense, useState, type ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import type { GcodeViewerProps } from "@/components/gcode-viewer";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { gcodePlatesOptions } from "@/lib/queries/file-plates";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { FileRead } from "@/types";

const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((module) => ({ default: module.GcodeViewer })),
);

export function GcodePreview({
  files,
  bedSizeFor,
  ToolpathViewer = GcodeViewer,
}: {
  files: FileRead[];
  bedSizeFor: (file: FileRead) => { x: number; y: number } | null;
  ToolpathViewer?: ComponentType<GcodeViewerProps>;
}) {
  useUiLocale();
  const sources = files.filter((file) => file.file_type === "gcode" || file.file_type === "3mf");
  const [selectedFile, setSelectedFile] = useState<number | null>(null);
  const [selectedPlate, setSelectedPlate] = useState<{ fileId: number; number: number } | null>(
    null,
  );
  const file =
    sources.find((source) => source.id === selectedFile) ??
    sources.find((source) => source.is_recommended) ??
    sources.findLast((source) => source.file_type === "gcode") ??
    sources[0];
  const embedded = file?.file_type === "3mf";
  const query = useQuery(gcodePlatesOptions(embedded ? file.id : null));
  if (file === undefined) return null;
  const numbers = query.data?.plates ?? [];
  const plate =
    selectedPlate?.fileId === file.id && numbers.includes(selectedPlate.number)
      ? selectedPlate.number
      : numbers[0];
  const url = embedded
    ? plate === undefined
      ? null
      : `/api/v1/files/${file.id}/embedded-gcode?plate_index=${plate}`
    : `/api/v1/files/${file.id}/toolpath`;

  return (
    <Localized>
      <div className="flex h-full min-h-80 w-full min-w-0 flex-col">
        <div className="relative min-h-60 flex-1">
          {embedded && query.isError ? (
            <div
              role="alert"
              className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center text-muted-foreground"
            >
              <p>{uiText("Couldn’t load sliced plates")}</p>
              <Button variant="outline" onClick={() => void query.refetch()}>
                {uiText("Retry")}
              </Button>
            </div>
          ) : embedded && query.isPending ? (
            <div
              role="status"
              className="flex h-full items-center justify-center gap-2 text-muted-foreground"
            >
              <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
              {uiText("Loading sliced plates…")}
            </div>
          ) : url ? (
            <Suspense fallback={<Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />}>
              <ToolpathViewer key={url} url={url} printerBedMm={bedSizeFor(file)} />
            </Suspense>
          ) : (
            <p
              role="status"
              className="flex h-full items-center justify-center p-4 text-center text-muted-foreground"
            >
              {uiText(
                "This 3MF has no sliced G-code. Slice it in Bambu Studio or OrcaSlicer, then upload the sliced file.",
              )}
            </p>
          )}
        </div>
        <div className="flex shrink-0 flex-col gap-2 border-t border-outline-variant bg-surface-container-low p-2">
          {sources.length > 1 ? (
            <label className="flex min-w-0 items-center gap-2 text-xs text-on-surface-variant">
              {uiText("G-code file")}
              <select
                value={file.id}
                onChange={(event) => setSelectedFile(Number(event.target.value))}
                className="min-w-0 flex-1 rounded border border-input bg-background px-2 py-1.5 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                {sources.map((source) => (
                  <option key={source.id} value={source.id}>
                    {source.original_filename}
                  </option>
                ))}
              </select>
            </label>
          ) : (
            <p className="truncate font-mono text-2xs text-on-surface-variant">
              {file.original_filename}
            </p>
          )}
          {embedded && numbers.length > 0 && (
            <div
              role="group"
              aria-label={uiText("Sliced plates")}
              className="flex min-w-0 gap-1.5 overflow-x-auto"
            >
              {numbers.map((number) => (
                <Button
                  key={number}
                  variant={plate === number ? "ghost" : "outline"}
                  size="sm"
                  aria-pressed={plate === number}
                  onClick={() => setSelectedPlate({ fileId: file.id, number })}
                  className={
                    plate === number ? "shrink-0 bg-accent text-accent-foreground" : "shrink-0"
                  }
                >
                  {uiText("Plate {value1}", { value1: String(number).padStart(2, "0") })}
                </Button>
              ))}
            </div>
          )}
        </div>
      </div>
    </Localized>
  );
}
