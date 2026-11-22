import { getAuthenticatedBlob } from "@/lib/api";
import { onAuthChange } from "@/lib/auth-store";

// Match HTTP/1.1's per-origin connection pool. Viewport admission bounds the
// work; primary cards still take every queued slot before background mosaics.
const MAX_DOWNLOADS = 6;
const MAX_INACTIVE_ENTRIES = 400;
const MAX_INACTIVE_BYTES = 32 * 1024 * 1024;
// The backend can admit as few as one archive preview at a time. Strip images
// share that reader with the selected slide and need their own transfer slot.
const ARCHIVE_IMAGE_PATH = /^\/api\/v1\/files\/\d+\/archive-images\//;

type AssetStatus = "queued" | "loading" | "ready" | "retired";
// Explicit preview intent takes the next slot. Visible covers and mosaics can
// fill the rest before hover-initiated work takes its single speculative slot.
const priorityOrder = { intent: 0, primary: 1, background: 2, speculative: 3 } as const;
export type AssetPriority = keyof typeof priorityOrder;
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
  priority: AssetPriority = "background";
  data: AssetData | null = null;
  references = 0;
  counted = false;

  readonly archiveImage: boolean;

  constructor(readonly path: string) {
    this.archiveImage = ARCHIVE_IMAGE_PATH.test(path);
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
let archiveDownload: AssetEntry | null = null;
let disposing = false;
const invalidationListeners = new Map<string, Set<() => void>>();

function touch(entry: AssetEntry): void {
  if (entries.get(entry.path) !== entry) return;
  entries.delete(entry.path);
  entries.set(entry.path, entry);
}

function releaseDownload(entry: AssetEntry): boolean {
  if (!entry.counted) return false;
  entry.counted = false;
  downloads -= 1;
  if (archiveDownload === entry) archiveDownload = null;
  return true;
}

function retire(entry: AssetEntry, reason: Error): void {
  if (entry.status === "retired") return;
  entry.status = "retired";
  if (entries.get(entry.path) === entry) entries.delete(entry.path);
  const queued = queue.indexOf(entry);
  if (queued !== -1) queue.splice(queued, 1);
  entry.controller.abort(reason);
  releaseDownload(entry);
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
    const next = queue.findIndex((entry) => !entry.archiveImage || archiveDownload === null);
    if (next === -1) break;
    if (
      queue[next].priority === "speculative" &&
      Array.from(entries.values()).some(
        (entry) => entry.status === "loading" && entry.priority === "speculative",
      )
    )
      break;
    const [entry] = queue.splice(next, 1);
    if (entry.status !== "queued") continue;
    entry.status = "loading";
    downloads += 1;
    entry.counted = true;
    if (entry.archiveImage) archiveDownload = entry;
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
        if (releaseDownload(entry)) pump();
      });
  }
}

function enqueue(entry: AssetEntry): void {
  if (entry.priority === "intent") {
    queue.unshift(entry);
    return;
  }
  const next = queue.findIndex(
    (queued) => priorityOrder[queued.priority] > priorityOrder[entry.priority],
  );
  if (next === -1) queue.push(entry);
  else queue.splice(next, 0, entry);
}

function promote(entry: AssetEntry, priority: AssetPriority): void {
  if (priorityOrder[priority] >= priorityOrder[entry.priority]) return;
  entry.priority = priority;
  const index = queue.indexOf(entry);
  if (index === -1) return;
  queue.splice(index, 1);
  enqueue(entry);
}

function getEntry(path: string, priority: AssetPriority): AssetEntry {
  const cached = entries.get(path);
  if (cached) {
    touch(cached);
    promote(cached, priority);
    return cached;
  }
  const entry = new AssetEntry(path);
  entry.priority = priority;
  entries.set(path, entry);
  enqueue(entry);
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
export function acquireAssetUrl(
  path: string,
  signal?: AbortSignal,
  priority: AssetPriority = "primary",
): AssetLease {
  if (signal?.aborted) return { url: Promise.reject(signal.reason), release() {} };
  const entry = getEntry(path, priority);
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

/** Promote already-owned work without adding another consumer or transfer. */
export function prioritizeCachedAsset(path: string): void {
  const entry = entries.get(path);
  if (!entry) return;
  promote(entry, "intent");
  pump();
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
