/** One toolpath at a time, from an ordinary Revision or a sliced 3MF plate. */
import { lazy, Suspense, useCallback, useLayoutEffect, useState, type ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { createPortal } from "react-dom";
import type { ViewerControls } from "@/components/stl-viewer";
import type { GcodeViewerProps } from "@/components/gcode-viewer";
import { PlatePicker } from "./plate-picker";
import { matchSlicedPlate } from "./plate-mapping";
import { ToolpathLayout } from "@/components/toolpath-layout";
import { Button } from "@/components/ui/button";
import { Localized } from "@/components/ui/localized";
import { gcodePlatesOptions } from "@/lib/queries/file-plates";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import type { ArchivePreviewImage } from "@/types/previews";
import type { FilePlate, FileRead } from "@/types";

const GcodeViewer = lazy(() =>
  import("@/components/gcode-viewer").then((module) => ({ default: module.GcodeViewer })),
);

export function GcodePreview({
  files,
  bedSizeFor,
  ToolpathViewer = GcodeViewer,
  selectedFileId,
  onSelectFile,
  sourcePlates = [],
  archiveImages = [],
  selectedSourcePlate = null,
  onSelectSourcePlate,
  onInspectSlicedPlate,
  plateContainer,
  onPlatePickerReady,
  viewerProps,
}: {
  files: FileRead[];
  bedSizeFor: (file: FileRead) => { x: number; y: number } | null;
  ToolpathViewer?: ComponentType<GcodeViewerProps>;
  selectedFileId?: number;
  onSelectFile?: (fileId: number) => void;
  sourcePlates?: FilePlate[];
  archiveImages?: ArchivePreviewImage[];
  selectedSourcePlate?: number | null;
  onInspectSlicedPlate?: (fileId: number, number: number) => void;
  onSelectSourcePlate?: (number: number | null) => void;
  plateContainer?: HTMLElement | null;
  onPlatePickerReady?: (ready: boolean) => void;
  viewerProps?: Omit<GcodeViewerProps, "url" | "printerBedMm">;
}) {
  useUiLocale();
  const sources = files.filter((file) => file.file_type === "gcode" || file.file_type === "3mf");
  const [layerContainer, setLayerContainer] = useState<HTMLDivElement | null>(null);
  const [viewContainer, setViewContainer] = useState<HTMLDivElement | null>(null);
  const [ready, setReady] = useState(false);
  const notifyControls = viewerProps?.onControlsReady;
  const connectControls = useCallback(
    (controls: ViewerControls | null) => {
      setReady(controls !== null);
      notifyControls?.(controls);
    },
    [notifyControls],
  );
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
  // Keep the source strip while the selected archive is loading, then hand it
  // off (or remove it for a single ordinary toolpath without sliced plates).
  const plateSelectionResolved = Boolean(plateContainer && (!embedded || !query.isPending));
  useLayoutEffect(() => {
    onPlatePickerReady?.(plateSelectionResolved);
    return () => onPlatePickerReady?.(false);
  }, [plateSelectionResolved, onPlatePickerReady]);
  if (file === undefined) return null;
  const numbers = query.data?.plates ?? [];
  const identities = query.data?.identities ?? [];
  const mapped = sourcePlates.map((source) => ({
    source,
    sliced: matchSlicedPlate(source, sourcePlates, identities),
  }));
  const selectedMapping = mapped.find(
    ({ source }) => source.number === selectedSourcePlate,
  )?.sliced;
  const plate =
    selectedMapping && numbers.includes(selectedMapping.number)
      ? selectedMapping.number
      : selectedPlate?.fileId === file.id && numbers.includes(selectedPlate.number)
        ? selectedPlate.number
        : numbers[0];
  const url = embedded
    ? plate === undefined
      ? null
      : `/api/v1/files/${file.id}/embedded-gcode?plate_index=${plate}`
    : `/api/v1/files/${file.id}/toolpath`;

  const platePicker = embedded && numbers.length > 0 && (
    <PlatePicker
      groupLabel={uiText("Sliced plates")}
      allowAll={false}
      plates={numbers.map((number) => {
        const match = mapped.find(({ sliced }) => sliced?.number === number);
        const identity = identities.find((item) => item.number === number);
        return {
          number,
          name: identity?.name ?? null,
          object_count:
            file.sliced_geometry === "gcode_only"
              ? null
              : (match?.source.object_count ?? identity?.object_count ?? null),
          image_url:
            match?.source.image_url ??
            archiveImages.find((image) => image.file_id === file.id && image.plate === number)
              ?.url ??
            null,
        };
      })}
      selected={plate ?? null}
      notice={
        selectedSourcePlate !== null && !selectedMapping
          ? uiText("The selected source plate could not be matched to a sliced plate.")
          : undefined
      }
      onSelect={(number) => {
        if (number === null) return;
        const match = mapped.find(({ sliced }) => sliced?.number === number);
        setSelectedPlate({ fileId: file.id, number });
        onInspectSlicedPlate?.(file.id, number);
        onSelectSourcePlate?.(match?.source.number ?? null);
      }}
    />
  );

  const sourceControls = sources.length > 1 && (
    <div className="toolpath-section toolpath-sources">
      <label className="toolpath-field">
        <span className="sr-only">{uiText("G-code file")}</span>
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
      </label>
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
      {plateContainer && platePicker && createPortal(platePicker, plateContainer)}
      <ToolpathLayout
        layers={
          <>
            {sourceControls}
            {plateContainer === undefined && platePicker}
            <div ref={setLayerContainer} />
          </>
        }
        view={<div ref={setViewContainer} />}
        viewAvailable={ready}
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
              controlsContainers={{ layers: layerContainer, view: viewContainer }}
              onControlsReady={connectControls}
            />
          </Suspense>
        )}
      </ToolpathLayout>
    </Localized>
  );
}
