"use client";

import { Suspense, useEffect, useLayoutEffect, useRef, type CSSProperties } from "react";
import { flushSync } from "react-dom";
import { Download, Loader2, Maximize2, X } from "lucide-react";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { cardRevisions } from "@/features/library/card-detail";
import { useModelDetail } from "@/features/library/model-detail";
import { getSessionVersion } from "@/lib/session-transport";
import { displayModelName } from "@/lib/display-name";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { downloadAuthenticatedFile } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useInspectorPreviewHeight } from "./use-inspector-preview-height";
import { userMessage } from "@/lib/errors";
import { lazyImport } from "@/lib/lazy-component";
import {
  clampInspectorWidth,
  LIBRARY_INSPECTOR_MIN_WIDTH,
  useInspectorMotion,
} from "./use-inspector-motion";
import { Button } from "@/components/ui/button";
import "./library-model-inspector.css";

const InspectorContent = lazyImport(() =>
  import("./library-model-inspector-content").then((module) => ({
    default: module.InspectorContent,
  })),
);

function InspectorLoading() {
  useUiLocale();
  return (
    <div className="library-inspector-body" role="status">
      <Loader2 className="h-5 w-5 animate-spin" />
      <span className="sr-only">{uiText("Loading...")}</span>
    </div>
  );
}

export function LibraryModelInspector({
  id,
  open,
  origin,
  canWrite,
  onClose,
  onOpenPage,
  onMotionSettled,
  resize,
  style,
}: {
  id: number;
  open: boolean;
  origin?: LibraryEntry;
  canWrite: boolean;
  onClose: () => void;
  onOpenPage?: () => void;
  onMotionSettled?: () => void;
  resize?: {
    current: number;
    max: number;
    locked: boolean;
    onResize: (width: number | null) => void;
  } | null;
  style?: CSSProperties;
}) {
  useUiLocale();
  const detail = useModelDetail(id);
  const title = useRef<HTMLHeadingElement>(null);
  // One panel serves every Model it is pointed at: switching Models swaps its
  // contents in place, so the panel itself only moves when it opens or closes.
  const { panel, present } = useInspectorMotion(open, onMotionSettled);
  const scroll = useRef<HTMLDivElement>(null);
  const previewSize = useInspectorPreviewHeight(scroll);
  const files = detail.data?.files ?? [];
  const revisions = cardRevisions(detail.data);
  const downloadFile = revisions.find((file) => file.is_recommended) ?? revisions[0] ?? files[0];
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startWidth: number;
    startCursor: string;
    startUserSelect: string;
  } | null>(null);
  const stopResize = () => {
    if (!drag.current) return;
    document.body.style.cursor = drag.current.startCursor;
    document.body.style.userSelect = drag.current.startUserSelect;
    drag.current = null;
  };
  const canResize = open && !!resize && !resize.locked;
  useLayoutEffect(() => stopResize, [canResize]);
  useEffect(() => {
    if (scroll.current) scroll.current.scrollTop = 0;
  }, [id]);
  useEffect(() => {
    if (open) title.current?.focus({ preventScroll: true });
  }, [id, open]);
  function widthHandle(edge: "panel" | "viewer") {
    if (!resize) return null;
    return (
      <div
        role="separator"
        aria-orientation="vertical"
        aria-label={uiText(edge === "panel" ? "Resize details panel" : "Resize preview width")}
        aria-valuemin={LIBRARY_INSPECTOR_MIN_WIDTH}
        aria-valuemax={resize.max}
        aria-valuenow={clampInspectorWidth(resize.current, resize.max)}
        aria-disabled={resize.locked || undefined}
        tabIndex={resize.locked ? -1 : 0}
        title={uiText("Drag to resize · Double-click to reset")}
        className="group absolute inset-y-0 -left-1 z-20 hidden w-2 touch-none cursor-col-resize focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring lg:block"
        onPointerDown={(event) => {
          if (!canResize || event.button !== 0 || drag.current) return;
          event.preventDefault();
          event.currentTarget.setPointerCapture(event.pointerId);
          drag.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startWidth: resize.current,
            startCursor: document.body.style.cursor,
            startUserSelect: document.body.style.userSelect,
          };
          document.body.style.cursor = "col-resize";
          document.body.style.userSelect = "none";
        }}
        onPointerMove={(event) => {
          const active = drag.current;
          if (!canResize || !active || event.pointerId !== active.pointerId) return;
          const delta = active.startX - event.clientX;
          if (delta === 0 && resize.current === active.startWidth) return;
          const width = clampInspectorWidth(active.startWidth + delta, resize.max);
          flushSync(() => resize.onResize(width));
        }}
        onLostPointerCapture={(event) => {
          if (event.pointerId === drag.current?.pointerId) stopResize();
        }}
        onPointerUp={(event) => {
          if (event.pointerId === drag.current?.pointerId) stopResize();
        }}
        onPointerCancel={(event) => {
          if (event.pointerId === drag.current?.pointerId) stopResize();
        }}
        onDoubleClick={() => {
          if (canResize) resize.onResize(null);
        }}
        onKeyDown={(event) => {
          if (!canResize) return;
          let width: number;
          if (event.key === "ArrowLeft") width = resize.current + 16;
          else if (event.key === "ArrowRight") width = resize.current - 16;
          else if (event.key === "Home") width = LIBRARY_INSPECTOR_MIN_WIDTH;
          else if (event.key === "End") width = resize.max;
          else return;
          event.preventDefault();
          resize.onResize(clampInspectorWidth(width, resize.max));
        }}
      >
        <span className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border group-hover:bg-foreground group-focus-visible:bg-foreground" />
        <span className="absolute left-1/2 top-1/2 h-10 w-1 -translate-x-1/2 -translate-y-1/2 bg-border group-hover:bg-foreground group-focus-visible:bg-foreground" />
      </div>
    );
  }
  return (
    <aside
      ref={panel}
      hidden={!present}
      inert={!open}
      data-state={open ? "open" : "closed"}
      id="model-drawer"
      aria-label={uiText("Model details")}
      className="library-model-inspector"
      style={style}
    >
      {widthHandle("panel")}
      <div ref={scroll} className="library-inspector-scroll">
        <header className="library-inspector-header">
          <h2 ref={title} tabIndex={-1} title={detail.data?.name}>
            {detail.data ? displayModelName(detail.data.name) : uiText("Model details")}
          </h2>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={uiText("Download")}
            title={uiText("Download")}
            disabled={!downloadFile}
            onClick={() => {
              if (!downloadFile) return;
              void downloadAuthenticatedFile(
                `/api/v1/files/${downloadFile.id}/download`,
                downloadFile.original_filename,
              ).catch((error) => toast.error(userMessage(error)));
            }}
          >
            <Download className="h-4 w-4" aria-hidden />
          </Button>
          <Button variant="ghost" size="icon-sm" asChild>
            <LibraryItemLink
              origin={origin}
              href={`/models/${id}`}
              aria-label={uiText("Open page")}
              onClick={(event) => {
                if (
                  !event.defaultPrevented &&
                  event.button === 0 &&
                  !event.ctrlKey &&
                  !event.metaKey &&
                  !event.altKey &&
                  !event.shiftKey
                )
                  onOpenPage?.();
              }}
              title={uiText("Open the full model page")}
            >
              <Maximize2 className="h-4 w-4" aria-hidden />
            </LibraryItemLink>
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={uiText("Close details")}
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </Button>
        </header>
        {detail.isError || !detail.active ? (
          <div className="library-inspector-body" role="alert">
            <p>{detail.error ? userMessage(detail.error) : uiText("Sign in to view this model")}</p>
            <Button variant="outline" size="xs" onClick={() => void detail.refetch()}>
              {uiText("Retry")}
            </Button>
          </div>
        ) : detail.data ? (
          <Suspense fallback={<InspectorLoading />}>
            <InspectorContent
              key={`${id}:${origin?.session ?? getSessionVersion()}`}
              model={detail.data}
              publish={detail.publish}
              active={open}
              previewSize={previewSize}
              widthHandle={widthHandle("viewer")}
              canWrite={canWrite}
              onClose={onClose}
            />
          </Suspense>
        ) : (
          <InspectorLoading />
        )}
      </div>
    </aside>
  );
}
