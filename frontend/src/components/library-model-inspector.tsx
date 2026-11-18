"use client";

import { Suspense, useEffect, useRef } from "react";
import { Loader2, Maximize2, X } from "lucide-react";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { useModelDetail } from "@/features/library/model-detail";
import { getSessionVersion } from "@/lib/session-transport";
import { displayModelName } from "@/lib/display-name";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { userMessage } from "@/lib/errors";
import { lazyImport } from "@/lib/lazy-component";
import { useInspectorMotion } from "./use-inspector-motion";
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
}: {
  id: number;
  open: boolean;
  origin?: LibraryEntry;
  canWrite: boolean;
  onClose: () => void;
  onOpenPage?: () => void;
}) {
  useUiLocale();
  const detail = useModelDetail(id);
  const title = useRef<HTMLHeadingElement>(null);
  // One panel serves every Model it is pointed at: switching Models swaps its
  // contents in place, so the panel itself only moves when it opens or closes.
  const { panel, present } = useInspectorMotion(open, id);
  useEffect(() => {
    if (open) title.current?.focus({ preventScroll: true });
  }, [id, open]);
  return (
    <aside
      ref={panel}
      hidden={!present}
      inert={!open}
      data-state={open ? "open" : "closed"}
      id="model-drawer"
      aria-label={uiText("Model details")}
      className="library-model-inspector"
    >
      <header className="library-inspector-header">
        <h2 ref={title} tabIndex={-1} title={detail.data?.name}>
          {detail.data ? displayModelName(detail.data.name) : uiText("Model details")}
        </h2>
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
          <Button variant="outline" size="sm" onClick={() => void detail.refetch()}>
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
            canWrite={canWrite}
            onClose={onClose}
          />
        </Suspense>
      ) : (
        <InspectorLoading />
      )}
    </aside>
  );
}
