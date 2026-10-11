"use client";

/** Wait for durable STL preparation before transferring bytes to the mesh parser. */
import { useEffect, useState } from "react";
import { getDerivedBlob } from "@/lib/api/request";
import { parseApiError } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { followModel } from "@/lib/events";

export type StlPreviewFailure =
  | "resource_limit"
  | "timeout"
  | "invalid_source"
  | "derivative_group_disabled"
  | "cancelled"
  | "worker_failed";

function failureCode(code: string): StlPreviewFailure {
  switch (code) {
    case "resource_limit":
    case "timeout":
    case "invalid_source":
    case "derivative_group_disabled":
    case "cancelled":
      return code;
    default:
      return "worker_failed";
  }
}

export type StlPreview =
  | { state: "pending" }
  | { state: "ready"; url: string }
  | { state: "failed"; reason: StlPreviewFailure };

export function stlPreviewMessage(reason: StlPreviewFailure): string {
  switch (reason) {
    case "resource_limit":
      return uiText("3D preview omitted due to memory limits");
    case "timeout":
      return uiText("3D preview preparation timed out");
    case "invalid_source":
      return uiText("This file cannot be converted to a 3D preview");
    case "derivative_group_disabled":
      return uiText("3D preview processing is disabled");
    case "cancelled":
      return uiText("3D preview preparation was cancelled");
    default:
      return uiText("Failed to load 3D preview");
  }
}

export function useStlPreview(
  url: string | null,
  modelId?: number,
  previewFetcher: typeof getDerivedBlob = getDerivedBlob,
): StlPreview {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    if (modelId === undefined) return;
    return followModel(modelId, (notice) => {
      if (
        notice.type === "resync" ||
        notice.type === "derivative_policy" ||
        (notice.type === "derivative" && notice.kind === "viewer_stl" && notice.state === "ready")
      )
        setRevision((value) => value + 1);
    });
  }, [modelId]);
  const [result, setResult] = useState<{
    source: string | null;
    revision: number;
    fetcher: typeof getDerivedBlob;
    preview: StlPreview;
  }>({
    source: url,
    fetcher: previewFetcher,
    revision,
    preview: { state: "pending" },
  });
  useEffect(() => {
    if (url === null) return;
    const source = url;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let blobUrl: string | undefined;
    async function load() {
      try {
        const response = await previewFetcher(source, controller.signal);
        if (controller.signal.aborted) return;
        if (!response.ready) {
          setResult({
            source: url,
            fetcher: previewFetcher,
            revision,
            preview: { state: "pending" },
          });
          timer = setTimeout(() => void load(), 1000);
          return;
        }
        if (controller.signal.aborted) return;
        blobUrl = URL.createObjectURL(response.blob);
        setResult({
          source: url,
          fetcher: previewFetcher,
          revision,
          preview: { state: "ready", url: blobUrl },
        });
      } catch (error) {
        if (!controller.signal.aborted)
          setResult({
            source: url,
            fetcher: previewFetcher,
            revision,
            preview: { state: "failed", reason: failureCode(parseApiError(error).code) },
          });
      }
    }
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
      if (blobUrl !== undefined) URL.revokeObjectURL(blobUrl);
    };
  }, [url, revision, previewFetcher]);
  return result.source === url && result.revision === revision && result.fetcher === previewFetcher
    ? result.preview
    : { state: "pending" };
}
