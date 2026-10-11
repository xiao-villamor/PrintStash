"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { acquireAssetUrl, onCachedAssetInvalidation, peekCachedAssetUrl } from "@/lib/asset-cache";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";

export type AuthenticatedAsset =
  | { status: "idle" | "pending" | "failed"; url: null }
  | { status: "ready"; url: string };

/** Protected image display ownership, optionally deferred until viewport admission. */
export function useAuthenticatedAsset(
  path: string | null | undefined,
  admitted = true,
): AuthenticatedAsset {
  const version = useSyncExternalStore(onAuthChange, getSessionVersion, () => 0);
  const [, refresh] = useState(0);
  const [invalidated, setInvalidated] = useState(0);
  const [failure, setFailure] = useState<{
    path: string;
    version: number;
    invalidated: number;
  } | null>(null);
  useEffect(() => {
    if (!path) return;
    return onCachedAssetInvalidation(path, () => setInvalidated((value) => value + 1));
  }, [path]);
  useEffect(() => {
    if (!path || !admitted) return;
    let alive = true;
    // Even a synchronous cache hit needs a lease, so eviction knows it is displayed.
    const lease = acquireAssetUrl(path);
    lease.url.then(
      () => {
        if (alive) refresh((value) => value + 1);
      },
      () => {
        if (alive) setFailure({ path, version, invalidated });
      },
    );
    return () => {
      alive = false;
      lease.release();
    };
  }, [path, admitted, version, invalidated]);

  if (!path || !admitted) return { status: "idle", url: null };
  const url = peekCachedAssetUrl(path);
  if (url !== null) return { status: "ready", url };
  const failed =
    failure?.path === path && failure.version === version && failure.invalidated === invalidated;
  return { status: failed ? "failed" : "pending", url: null };
}

export function useAuthenticatedAssetUrl(
  path: string | null | undefined,
  admitted = true,
): string | null {
  return useAuthenticatedAsset(path, admitted).url;
}
