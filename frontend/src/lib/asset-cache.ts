import { getAuthenticatedBlob } from "@/lib/api";
import { onAuthChange } from "@/lib/auth-store";

const MAX_DOWNLOADS = 4;
const MAX_INACTIVE_ENTRIES = 400;
const MAX_INACTIVE_BYTES = 32 * 1024 * 1024;

type AssetStatus = "queued" | "loading" | "ready" | "retired";
interface AssetData {
  url: string;
  bytes: number;
}

class AssetEntry {
  readonly controller = new AbortController();
  readonly promise: Promise<string>;
  readonly resolve: (url: string) => void;
  readonly reject: (reason: Error) => void;
  status: AssetStatus = "queued";
  data: AssetData | null = null;
  references = 0;
  counted = false;

  constructor(readonly path: string) {
    let resolve!: (url: string) => void;
    let reject!: (reason: Error) => void;
    this.promise = new Promise<string>((accept, refuse) => {
      resolve = accept;
      reject = refuse;
    });
    this.resolve = resolve;
    this.reject = reject;
  }
}

const entries = new Map<string, AssetEntry>();
const queue: AssetEntry[] = [];
let downloads = 0;
let disposing = false;
const invalidationListeners = new Map<string, Set<() => void>>();

function touch(entry: AssetEntry): void {
  if (entries.get(entry.path) !== entry) return;
  entries.delete(entry.path);
  entries.set(entry.path, entry);
}

function retire(entry: AssetEntry, reason: Error): void {
  if (entry.status === "retired") return;
  entry.status = "retired";
  if (entries.get(entry.path) === entry) entries.delete(entry.path);
  const queued = queue.indexOf(entry);
  if (queued !== -1) queue.splice(queued, 1);
  entry.controller.abort(reason);
  if (entry.counted) {
    entry.counted = false;
    downloads -= 1;
  }
  entry.reject(reason);
  if (entry.data) URL.revokeObjectURL(entry.data.url);
  pump();
}

function evictInactive(): void {
  let count = 0;
  let bytes = 0;
  for (const entry of entries.values()) {
    if (entry.references === 0 && entry.data) {
      count += 1;
      bytes += entry.data.bytes;
    }
  }
  for (const entry of entries.values()) {
    if (count <= MAX_INACTIVE_ENTRIES && bytes <= MAX_INACTIVE_BYTES) break;
    if (entry.references !== 0 || !entry.data) continue;
    count -= 1;
    bytes -= entry.data.bytes;
    retire(entry, new DOMException("asset_evicted", "AbortError"));
  }
}

function pump(): void {
  if (disposing) return;
  while (downloads < MAX_DOWNLOADS && queue.length > 0) {
    const entry = queue.shift()!;
    if (entry.status !== "queued") continue;
    entry.status = "loading";
    downloads += 1;
    entry.counted = true;
    getAuthenticatedBlob(entry.path, entry.controller.signal)
      .then((blob) => {
        if (entries.get(entry.path) !== entry || entry.controller.signal.aborted) return;
        entry.data = { url: URL.createObjectURL(blob), bytes: blob.size };
        entry.status = "ready";
        touch(entry);
        entry.resolve(entry.data.url);
        evictInactive();
      })
      .catch((reason: Error) => retire(entry, reason))
      .finally(() => {
        if (entry.counted) {
          entry.counted = false;
          downloads -= 1;
          pump();
        }
      });
  }
}

function getEntry(path: string): AssetEntry {
  const cached = entries.get(path);
  if (cached) {
    touch(cached);
    return cached;
  }
  const entry = new AssetEntry(path);
  entries.set(path, entry);
  queue.push(entry);
  return entry;
}

function releaseReference(entry: AssetEntry): void {
  entry.references -= 1;
  if (entry.status === "retired") return;
  if (entry.references === 0) {
    if (!entry.data) retire(entry, new DOMException("asset_consumer_left", "AbortError"));
    else {
      touch(entry);
      evictInactive();
    }
  }
}

export interface AssetLease {
  readonly url: Promise<string>;
  /** Release once the consumer stops displaying or awaiting the image. Idempotent. */
  release(): void;
}

/** Own a share of protected bytes; other consumers survive this caller's cancellation. */
export function acquireAssetUrl(path: string, signal?: AbortSignal): AssetLease {
  if (signal?.aborted) return { url: Promise.reject(signal.reason), release() {} };
  const entry = getEntry(path);
  entry.references += 1;
  let active = true;
  let reject!: (reason: Error) => void;
  const url = new Promise<string>((resolve, refuse) => {
    reject = refuse;
    entry.promise.then(resolve, refuse);
  });
  const release = (reason: Error = new DOMException("asset_consumer_left", "AbortError")) => {
    if (!active) return;
    active = false;
    signal?.removeEventListener("abort", cancel);
    reject(reason);
    releaseReference(entry);
  };
  const cancel = () => release(signal?.reason);
  signal?.addEventListener("abort", cancel, { once: true });
  pump();
  return { url, release };
}

/** Synchronous reuse; display consumers must still acquire a lease in their effect. */
export function peekCachedAssetUrl(path: string): string | null {
  const entry = entries.get(path);
  if (!entry?.data) return null;
  touch(entry);
  return entry.data.url;
}

/** A known server-side replacement must fetch fresh bytes on the next acquisition. */
export function invalidateCachedAsset(path: string): void {
  const entry = entries.get(path);
  if (entry) retire(entry, new Error("asset_request_invalidated"));
  for (const listener of invalidationListeners.get(path) ?? []) listener();
}

/** Tell mounted consumers a known replacement requires a new acquisition. */
export function onCachedAssetInvalidation(path: string, listener: () => void): () => void {
  let subscribers = invalidationListeners.get(path);
  if (!subscribers) {
    subscribers = new Set();
    invalidationListeners.set(path, subscribers);
  }
  subscribers.add(listener);
  return () => {
    subscribers.delete(listener);
    if (subscribers.size === 0) invalidationListeners.delete(path);
  };
}

/** Encoded Blob bytes only; browser image decoding has its own allocation. */
export function getAssetCacheStats() {
  let liveBytes = 0;
  let inactiveBytes = 0;
  let inactiveEntries = 0;
  for (const entry of entries.values()) {
    if (!entry.data) continue;
    if (entry.references > 0) liveBytes += entry.data.bytes;
    else {
      inactiveBytes += entry.data.bytes;
      inactiveEntries += 1;
    }
  }
  return { downloads, queued: queue.length, liveBytes, inactiveBytes, inactiveEntries };
}

/** Dispose private bytes and work while leaving identity validation to its owner. */
export function disposeCachedAssets(): void {
  disposing = true;
  try {
    for (const entry of entries.values()) {
      retire(entry, new DOMException("request_session_changed", "AbortError"));
    }
  } finally {
    disposing = false;
  }
  for (const listeners of invalidationListeners.values()) {
    for (const listener of listeners) listener();
  }
}

onAuthChange(disposeCachedAssets);
