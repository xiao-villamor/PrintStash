import { getAuthenticatedBlob } from "@/lib/api";

/**
 * Session cache for authenticated asset blobs (thumbnails).
 *
 * Thumbnails are protected, so they're fetched with a bearer header and turned
 * into object URLs (`URL.createObjectURL`). The old per-component hook re-fetched
 * the blob on every mount and revoked the object URL on unmount, so scrolling a
 * card out and back, paginating, or re-entering a folder paid for the same image
 * again (even if only a cheap 304) and recreated its object URL — which is a big
 * part of why the grid felt slow and the thumbnails "popped".
 *
 * This module keeps one object URL per asset path for the page session:
 *  - In-flight fetches are deduped, so N cards sharing a thumbnail trigger one
 *    request.
 *  - Resolved object URLs are reused synchronously (`peekCachedAssetUrl`), so a
 *    re-mount shows the image immediately instead of flashing empty.
 *  - An LRU cap bounds memory; evicted URLs are revoked.
 *
 * Trade-off: a thumbnail that changes server-side mid-session (re-upload reusing
 * a file id) stays cached until reload or an explicit `invalidateCachedAsset`.
 * Acceptable for previews; call `invalidateCachedAsset` after a known change.
 */

// Bounds memory: each entry holds a decoded image blob alive via its object URL.
// A few hundred small previews is a handful of MB — plenty for deep scrolling
// without unbounded growth.
const CACHE_LIMIT = 400;

// Insertion order doubles as LRU order: re-reading a key deletes + re-sets it to
// move it to the most-recent end.
const urlCache = new Map<string, string>();
const inflight = new Map<string, Promise<string>>();

/** Synchronously return a cached object URL, or null if not resolved yet. */
export function peekCachedAssetUrl(path: string): string | null {
  const url = urlCache.get(path);
  if (url === undefined) return null;
  // Touch for LRU.
  urlCache.delete(path);
  urlCache.set(path, url);
  return url;
}

/** Fetch (or reuse) the object URL for an authenticated asset path. */
export function getCachedAssetUrl(path: string): Promise<string> {
  const cached = peekCachedAssetUrl(path);
  if (cached) return Promise.resolve(cached);

  const pending = inflight.get(path);
  if (pending) return pending;

  const promise = getAuthenticatedBlob(path)
    .then((blob) => {
      const url = URL.createObjectURL(blob);
      urlCache.set(path, url);
      inflight.delete(path);
      evictIfNeeded();
      return url;
    })
    .catch((err) => {
      inflight.delete(path);
      throw err;
    });

  inflight.set(path, promise);
  return promise;
}

/** Drop a single asset from the cache (e.g. after it was re-uploaded). */
export function invalidateCachedAsset(path: string): void {
  const url = urlCache.get(path);
  if (url !== undefined) {
    URL.revokeObjectURL(url);
    urlCache.delete(path);
  }
  inflight.delete(path);
}

function evictIfNeeded(): void {
  while (urlCache.size > CACHE_LIMIT) {
    const oldest = urlCache.keys().next().value;
    if (oldest === undefined) break;
    const url = urlCache.get(oldest);
    urlCache.delete(oldest);
    if (url !== undefined) URL.revokeObjectURL(url);
  }
}
