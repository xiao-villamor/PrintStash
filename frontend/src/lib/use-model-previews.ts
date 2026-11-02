import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import {
  acquireAssetUrl,
  onCachedAssetInvalidation,
  prioritizeCachedAsset,
  type AssetLease,
} from "@/lib/asset-cache";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import type { PreviewImage, PreviewViewId } from "@/types/previews";

export const PREVIEW_INTENT_MS = 350;
export type PreviewSelection = PreviewViewId | "cover";

interface ConnectionBudget {
  saveData?: boolean;
  effectiveType?: string;
}
declare global {
  interface Navigator {
    connection?: ConnectionBudget;
  }
}

/** Data saving and slow links permit only explicit selections. */
export function previewTransferPolicy() {
  const connection = navigator.connection;
  const constrained =
    connection?.saveData === true ||
    connection?.effectiveType === "slow-2g" ||
    connection?.effectiveType === "2g";
  return {
    hover: !constrained && window.matchMedia("(hover: hover) and (pointer: fine)").matches,
    neighbor: !constrained,
  };
}

async function decode(url: string) {
  const image = new Image();
  image.src = url;
  if (image.decode) await image.decode();
}

/**
 * Owns generated views only; the cover is presented by `ProtectedThumbnail`.
 * The displayed view lease stays until the latest selected view has decoded.
 */
export function useModelPreviews(cover: string | null, views: PreviewImage[], admitted: boolean) {
  const [mountedSession] = useState(getSessionVersion);
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [selection, setSelection] = useState<PreviewSelection>("cover");
  const [attempt, setAttempt] = useState(0);
  const [displayed, setDisplayed] = useState<{
    id: PreviewSelection;
    url: string;
    session: number;
  } | null>(null);
  const [failure, setFailure] = useState<{ path: string; attempt: number } | null>(null);
  const displayedLease = useRef<AssetLease | null>(null);
  const warming = useRef(new Map<string, AssetLease>());
  const intent = useRef<ReturnType<typeof setTimeout> | null>(null);
  const available = selection === "cover" || views.some((view) => view.id === selection);
  const selected =
    available && (selection !== "cover" || cover)
      ? selection
      : cover
        ? "cover"
        : (views[0]?.id ?? "cover");
  const path = selected === "cover" ? null : views.find((view) => view.id === selected)?.url;
  const active = session === mountedSession;

  useEffect(() => {
    if (!active || !admitted || !path) return;
    let alive = true;
    let adopted = false;
    const lease = acquireAssetUrl(path, undefined, "intent");
    lease.url
      .then(async (url) => {
        await decode(url);
        if (!alive || getSessionVersion() !== mountedSession) return;
        const previous = displayedLease.current;
        displayedLease.current = lease;
        adopted = true;
        setDisplayed({ id: selected, url, session: mountedSession });
        previous?.release();
      })
      .catch(() => {
        if (alive) setFailure({ path, attempt });
      });
    return () => {
      alive = false;
      if (!adopted) lease.release();
    };
  }, [active, admitted, path, selected, attempt, mountedSession]);

  useEffect(() => {
    if (!path) return;
    return onCachedAssetInvalidation(path, () => setAttempt((value) => value + 1));
  }, [path]);

  function cancelIntent() {
    if (intent.current !== null) clearTimeout(intent.current);
    intent.current = null;
  }

  useEffect(
    () => () => {
      cancelIntent();
      displayedLease.current?.release();
      for (const lease of warming.current.values()) lease.release();
      warming.current.clear();
    },
    [],
  );

  function warm(
    view: PreviewImage | undefined,
    priority: "intent" | "speculative" = "speculative",
  ) {
    if (!active || !view) return;
    if (warming.current.has(view.url)) {
      if (priority === "intent") prioritizeCachedAsset(view.url);
      return;
    }
    const lease = acquireAssetUrl(view.url, undefined, priority);
    warming.current.set(view.url, lease);
    lease.url
      .then(decode)
      .catch(() => {})
      .finally(() => {
        lease.release();
        if (warming.current.get(view.url) === lease) warming.current.delete(view.url);
      });
  }
  function select(id: PreviewSelection) {
    if (id === "cover") {
      // The cover presents itself; a retained view must not reappear later.
      displayedLease.current?.release();
      displayedLease.current = null;
      setDisplayed(null);
    }
    warm(
      views.find((view) => view.id === id),
      "intent",
    );
    setSelection(id);
    setAttempt((value) => value + 1);
    // A deliberate interaction may warm one neighbor, including on touch.
    if (!previewTransferPolicy().neighbor || id === "cover") return;
    const ring = views.filter((view) => view.role === "orbit");
    const index = ring.findIndex((view) => view.id === id);
    if (index >= 0) warm(ring[(index + 1) % ring.length]);
  }
  function destination(delta: number) {
    const ring = views.filter((view) => view.role === "orbit");
    const index = ring.findIndex((view) => view.id === selected);
    return ring[
      index < 0 ? (delta < 0 ? ring.length - 1 : 0) : (index + delta + ring.length) % ring.length
    ];
  }
  function hover() {
    cancelIntent();
    if (!previewTransferPolicy().hover) return;
    intent.current = setTimeout(() => {
      warm(destination(1));
      warm(destination(-1));
    }, PREVIEW_INTENT_MS);
  }
  function prioritize(view: PreviewImage | undefined) {
    if (previewTransferPolicy().hover) warm(view, "intent");
  }
  return {
    selected,
    displayed: active && displayed?.session === session ? displayed : null,
    failed: failure !== null && failure.path === path && failure.attempt === attempt,
    select,
    destination,
    hover,
    cancelIntent,
    prioritize,
    retry: () => setAttempt((value) => value + 1),
  };
}
