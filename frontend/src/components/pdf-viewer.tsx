"use client";

import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";
import { ChevronLeft, ChevronRight, Loader2, ZoomIn, ZoomOut } from "lucide-react";

import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

// Vite bundles the worker from the pinned pdfjs-dist (matches react-pdf's copy).
pdfjs.GlobalWorkerOptions.workerSrc =
  new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString() +
  "?cache=pdfjs-worker-v2";

const btn =
  "flex items-center justify-center p-1.5 rounded border border-border bg-background text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-default";

export function PdfViewer({ file }: { file: string }) {
  const wrapRef = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0);
  const [numPages, setNumPages] = useState(0);
  const [page, setPage] = useState(1);
  const [scale, setScale] = useState(1);

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
        <Document
          file={file}
          onLoadSuccess={({ numPages }) => setNumPages(numPages)}
          loading={
            <div className="flex items-center justify-center py-16 text-muted-foreground">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          }
          error={<p className="py-16 text-sm text-muted-foreground">Could not render this PDF.</p>}
        >
          {pageWidth && (
            <Page pageNumber={page} width={pageWidth} className="shadow-lg [&>canvas]:rounded-sm" />
          )}
        </Document>
      </div>
    </div>
  );
}
