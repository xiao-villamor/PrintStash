"use client";

import { useEffect, useState, useSyncExternalStore } from "react";
import { acquireAssetUrl, onCachedAssetInvalidation, peekCachedAssetUrl } from "@/lib/asset-cache";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";

/** Protected image display ownership, optionally deferred until viewport admission. */
export function useAuthenticatedAssetUrl(
  path: string | null | undefined,
  admitted = true,
): string | null {
  const version = useSyncExternalStore(onAuthChange, getSessionVersion, () => 0);
  const [, refresh] = useState(0);
  const [invalidated, setInvalidated] = useState(0);
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
        if (alive) refresh((value) => value + 1);
      },
    );
    return () => {
      alive = false;
      lease.release();
    };
  }, [path, admitted, version, invalidated]);

  if (!path || !admitted) return null;
  return peekCachedAssetUrl(path);
}
