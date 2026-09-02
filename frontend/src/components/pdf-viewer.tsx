"use client";

import { type ComponentProps, type ReactNode, useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { ChevronLeft, ChevronRight, Loader2, ZoomIn, ZoomOut } from "lucide-react";

import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Use PDF.js's compatibility worker on every browser. Real Android browsers can
// fetch the modern worker successfully but still fail while starting it, and
// PDF.js cannot recover once that worker has failed on a page.
pdfjs.GlobalWorkerOptions.workerSrc =
  new URL("pdfjs-dist/legacy/build/pdf.worker.min.mjs", import.meta.url).toString() +
  "?cache=pdfjs-worker-compat-v1";

type DocumentRenderer = (props: ComponentProps<typeof Document>) => ReactNode;

const defaultDocumentRenderer: DocumentRenderer = (props) => <Document {...props} />;

const btn =
  "flex items-center justify-center p-1.5 rounded border border-border bg-background text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-default";

export function PdfViewer({
  file,
  renderDocument = defaultDocumentRenderer,
}: {
  file: Blob;
  renderDocument?: DocumentRenderer;
}) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const go = (d: number) => setPage((p) => Math.min(numPages || 1, Math.max(1, p + d)));
  const zoom = (d: number) => setScale((s) => Math.min(3, Math.max(0.5, +(s + d).toFixed(2))));

  // Fit page to the container width, then let zoom scale up/down from there.
  const pageWidth = width > 0 ? Math.max(200, width - 32) * scale : undefined;

  const handleLoadError = (error: Error) => {
    setLoadError(error.message || error.name);
  };

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center gap-2 mb-3">
        <button className={btn} onClick={() => go(-1)} disabled={page <= 1} title="Previous page">
          <ChevronLeft className="w-4 h-4" />
        </button>
        <span className="text-xs font-mono text-muted-foreground tabular-nums">
          {page} / {numPages || "…"}
        </span>
        <button
          className={btn}
          onClick={() => go(1)}
          disabled={!numPages || page >= numPages}
          title="Next page"
        >
          <ChevronRight className="w-4 h-4" />
        </button>
        <div className="ml-auto flex items-center gap-2">
          <button
            className={btn}
            onClick={() => zoom(-0.25)}
            disabled={scale <= 0.5}
            title="Zoom out"
          >
            <ZoomOut className="w-4 h-4" />
          </button>
          <span className="text-xs font-mono text-muted-foreground tabular-nums w-10 text-center">
            {Math.round(scale * 100)}%
          </span>
          <button className={btn} onClick={() => zoom(0.25)} disabled={scale >= 3} title="Zoom in">
            <ZoomIn className="w-4 h-4" />
          </button>
        </div>
      </div>

      <div
        ref={wrapRef}
        className="flex-1 min-h-0 overflow-auto rounded border border-border bg-muted/40 p-4 flex justify-center"
      >
        {loadError ? (
          <div className="py-16 text-center text-sm text-muted-foreground">
            <p>Could not render this PDF.</p>
            <p className="mt-2 max-w-md break-words font-mono text-xs">{loadError}</p>
          </div>
        ) : (
          renderDocument({
            file,
            onLoadError: handleLoadError,
            onSourceError: handleLoadError,
            onLoadSuccess: ({ numPages }) => {
              setLoadError(null);
              setNumPages(numPages);
            },
            loading: (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ),
            error: (
              <div className="flex items-center justify-center py-16 text-muted-foreground">
                <Loader2 className="w-5 h-5 animate-spin" />
              </div>
            ),
            children: pageWidth ? (
              <Page
                pageNumber={page}
                width={pageWidth}
                className="shadow-lg [&>canvas]:rounded-sm"
              />
            ) : null,
          })
        )}
      </div>
    </div>
  );
}
