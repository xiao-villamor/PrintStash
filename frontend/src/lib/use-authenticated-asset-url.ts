"use client";

import { useEffect, useState } from "react";

import { getCachedAssetUrl, peekCachedAssetUrl } from "@/lib/asset-cache";

/** The outcome of one background fetch, tagged with the path it was started for. */
interface ResolvedAsset {
  path: string;
  url: string | null;
}

export function useAuthenticatedAssetUrl(path: string | null | undefined): string | null {
  // Only a *completed background fetch* is state; everything the session cache
  // already knows is read during render instead, so switching paths needs no
  // reset pass.
  const [resolved, setResolved] = useState<ResolvedAsset | null>(null);

  useEffect(() => {
    if (!path || peekCachedAssetUrl(path)) return;
    let alive = true;
    getCachedAssetUrl(path)
      .then((url) => {
        if (alive) setResolved({ path, url });
      })
      .catch(() => {
        if (alive) setResolved({ path, url: null });
      });

    // Object URLs are owned by the cache (shared across components and reused
    // across mounts), so we no longer revoke on unmount — the cache evicts.
    return () => {
      alive = false;
    };
  }, [path]);

  if (!path) return null;
  // Reading the cache here (rather than seeding state from it) is what makes a
  // thumbnail that's already been fetched show instantly on re-mount
  // (re-scroll, pagination, folder revisit) instead of flashing empty and
  // fading in again.
  const cached = peekCachedAssetUrl(path);
  if (cached) return cached;
  // A result carried over from a previous `path` is not this path's URL.
  return resolved?.path === path ? resolved.url : null;
}
