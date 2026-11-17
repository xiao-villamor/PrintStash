/** One toolpath at a time, from an ordinary Revision or a sliced 3MF plate. */
import { lazy, Suspense, useState, type ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Layers, Loader2 } from "lucide-react";
import type { GcodeViewerProps } from "@/components/gcode-viewer";
import { ToolpathLayout } from "@/components/toolpath-layout";
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
  selectedFileId,
  onSelectFile,
  viewerProps,
}: {
  files: FileRead[];
  bedSizeFor: (file: FileRead) => { x: number; y: number } | null;
  ToolpathViewer?: ComponentType<GcodeViewerProps>;
  selectedFileId?: number;
  onSelectFile?: (fileId: number) => void;
  viewerProps?: Omit<GcodeViewerProps, "url" | "printerBedMm">;
}) {
  useUiLocale();
  const sources = files.filter((file) => file.file_type === "gcode" || file.file_type === "3mf");
  const [controlsContainer, setControlsContainer] = useState<HTMLDivElement | null>(null);
  const [selectedFile, setSelectedFile] = useState<number | null>(null);
  const [selectedPlate, setSelectedPlate] = useState<{ fileId: number; number: number } | null>(
    null,
  );
  const file =
    sources.find((source) => source.id === (selectedFileId ?? selectedFile)) ??
    sources.find((source) => source.is_recommended) ??
    sources.findLast((source) => source.file_type === "gcode") ??
    sources[0];
  const embedded = file?.original_filename.toLowerCase().endsWith(".3mf") === true;
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

  const sourceControls = (
    <div className="toolpath-section">
      <label className="toolpath-field">
        <span className="toolpath-section-title">{uiText("G-code file")}</span>
        {sources.length > 1 ? (
          <select
            value={file.id}
            onChange={(event) => {
              const id = Number(event.target.value);
              setSelectedFile(id);
              onSelectFile?.(id);
            }}
          >
            {sources.map((source) => (
              <option key={source.id} value={source.id}>
                {source.original_filename}
              </option>
            ))}
          </select>
        ) : (
          <span className="break-all text-xs text-foreground">{file.original_filename}</span>
        )}
      </label>
      {embedded && numbers.length > 0 && (
        <div role="group" aria-label={uiText("Sliced plates")} className="toolpath-plates">
          <div className="flex items-center justify-between gap-2">
            <span className="toolpath-section-title">{uiText("Sliced plates")}</span>
            <span className="font-mono text-xs text-muted-foreground">{numbers.length}</span>
          </div>
          <div className="toolpath-plate-list">
            {numbers.map((number) => (
              <Button
                key={number}
                variant="outline"
                aria-pressed={plate === number}
                onClick={() => setSelectedPlate({ fileId: file.id, number })}
                className="toolpath-plate-card"
              >
                <span className="toolpath-plate-number" aria-hidden>
                  <Layers className="h-4 w-4" />
                  {String(number).padStart(2, "0")}
                </span>
                <span className="min-w-0 flex-1 text-left">
                  {uiText("Plate {value1}", { value1: String(number).padStart(2, "0") })}
                </span>
                {plate === number && <Check className="h-4 w-4 shrink-0" aria-hidden />}
              </Button>
            ))}
          </div>
        </div>
      )}
    </div>
  );

  let unavailable;
  if (embedded && query.isError) {
    unavailable = (
      <div role="alert" className="toolpath-notice">
        <p>{uiText("Couldn’t load sliced plates")}</p>
        <Button variant="outline" onClick={() => void query.refetch()}>
          {uiText("Retry")}
        </Button>
      </div>
    );
  } else if (embedded && query.isPending) {
    unavailable = (
      <div role="status" className="toolpath-notice">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
        {uiText("Loading sliced plates…")}
      </div>
    );
  } else if (url === null) {
    unavailable = (
      <p role="status" className="toolpath-notice">
        {uiText(
          "This 3MF has no sliced G-code. Slice it in Bambu Studio or OrcaSlicer, then upload the sliced file.",
        )}
      </p>
    );
  }

  return (
    <Localized>
      <ToolpathLayout
        sidebar={
          <>
            {sourceControls}
            <div ref={setControlsContainer} />
          </>
        }
      >
        {unavailable || url === null ? (
          unavailable
        ) : (
          <Suspense
            fallback={
              <div role="status" className="toolpath-notice">
                <Loader2 className="h-8 w-8 animate-spin" aria-hidden />
                {uiText("Loading sliced plates…")}
              </div>
            }
          >
            <ToolpathViewer
              {...viewerProps}
              key={url}
              url={url}
              printerBedMm={bedSizeFor(file)}
              controlsContainer={controlsContainer}
            />
          </Suspense>
        )}
      </ToolpathLayout>
    </Localized>
  );
}
