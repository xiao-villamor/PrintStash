"use client";
import { previewsInFlight, type ModelPreviews } from "@/types/previews";

import { useEffect, useMemo, useRef } from "react";

import { followModel } from "@/lib/events";

/**
 * Most Models one grid follows for a thumbnail still to come. The server caps
 * subscriptions per socket, and a page of placeholders is a fresh upload, not a
 * whole library.
 */
export const MAX_FOLLOWED = 24;

/**
 * How long arrivals are gathered into one refresh. Following a page of
 * placeholders is acknowledged once per Model, and a bulk upload settles its
 * thumbnails together; each refresh refetches every list query, so a burst
 * must cost one.
 */
export const ARRIVAL_COALESCE_MS = 250;

/**
 * Refresh a list when a thumbnail it is waiting for lands.
 *
 * A fresh upload's Model appears before its thumbnail is derived; the card
 * shows a placeholder. This follows the Models on screen that still have no
 * thumbnail (up to `MAX_FOLLOWED`) and calls `onArrival` when one of their
 * thumbnails settles, when the server confirms it is following one (a
 * thumbnail that settled before then was announced to nobody), or after a
 * reconnect, so the list refetches through its authorized query instead of
 * the user reloading.
 */
export function useThumbnailArrivals(
  models: ReadonlyArray<{ id: number; thumbnail_url: string | null; previews: ModelPreviews }>,
  onArrival: () => void,
): void {
  // A stable key of the waited-for ids, so re-rendering the same page does not
  // resubscribe every channel.
  const waiting = useMemo(
    () =>
      models
        .filter((model) => model.thumbnail_url === null || previewsInFlight(model.previews))
        .slice(0, MAX_FOLLOWED)
        .map((model) => model.id)
        .join(","),
    [models],
  );

  // The latest callback, so a new closure per render does not resubscribe.
  const latest = useRef(onArrival);
  useEffect(() => {
    latest.current = onArrival;
  });

  useEffect(() => {
    if (!waiting) return;
    let pending: ReturnType<typeof setTimeout> | null = null;
    const arrived = () => {
      if (pending !== null) return;
      pending = setTimeout(() => {
        pending = null;
        latest.current();
      }, ARRIVAL_COALESCE_MS);
    };
    const stops = waiting.split(",").map((id) =>
      followModel(Number(id), (notice) => {
        if (notice.type === "resync" || notice.type === "subscribed") return arrived();
        if (
          notice.type === "derivative" &&
          (notice.kind === "thumbnail" || notice.kind === "previews")
        ) {
          if (["ready", "skipped", "failed", "cancelled"].includes(notice.state)) arrived();
        }
      }),
    );
    return () => {
      if (pending !== null) clearTimeout(pending);
      stops.forEach((stop) => stop());
    };
  }, [waiting]);
}
