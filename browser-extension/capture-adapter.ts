const FIELD_LIMITS = {
  title: 512,
  description: 64 * 1024,
  instructions: 128 * 1024,
  creator_name: 512,
  creator_id: 255,
  creator_url: 2048,
  license_code: 255,
  license_url: 2048,
  license_text: 64 * 1024,
  attribution_text: 64 * 1024,
  published_at: 64,
  updated_at: 64,
};

const ADAPTER_VERSION = "browser-visible-v1";

export const JSON_LD_MAX_SCRIPTS = 32;
export const JSON_LD_MAX_SCRIPT_BYTES = 256 * 1024;
export const JSON_LD_MAX_TOTAL_BYTES = 1024 * 1024;
export const JSON_LD_MAX_DEPTH = 12;
export const JSON_LD_MAX_NODES = 4_096;
export const JSON_LD_MAX_OBJECTS = 2_048;
export const JSON_LD_MAX_QUEUE = 2_048;

/** Read a provider metadata response without allowing an unbounded body into memory. */
export async function readBoundedMetadataResponse(
  response: Response,
  maximumBytes: number,
  signal?: AbortSignal,
): Promise<string> {
  if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  const contentLengthHeader = response.headers.get("Content-Length");
  if (contentLengthHeader !== null) {
    const contentLength = Number(contentLengthHeader);
    if (!Number.isSafeInteger(contentLength) || contentLength < 0 || contentLength > maximumBytes) {
      throw new Error("response too large");
    }
  }
  if (!response.body) throw new Error("response body missing");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let total = 0;
  let body = "";
  const abortReader = () => {
    void reader.cancel();
  };
  signal?.addEventListener("abort", abortReader, { once: true });
  try {
    while (true) {
      if (signal?.aborted) {
        await reader.cancel();
        throw new DOMException("The operation was aborted.", "AbortError");
      }
      const next = await reader.read();
      if (next.done) break;
      total += next.value.byteLength;
      if (total > maximumBytes) {
        await reader.cancel();
        throw new Error("response too large");
      }
      body += decoder.decode(next.value, { stream: true });
    }
    return body + decoder.decode();
  } finally {
    signal?.removeEventListener("abort", abortReader);
    reader.releaseLock();
  }
}

const PROVIDER_CODES = {
  Printables: "printables",
  MakerWorld: "makerworld",
  Thingiverse: "thingiverse",
  Cults: "cults",
};
type JsonRecord = Record<string, unknown>;

function plainText(value: unknown, maximum: number): string | null {
  if (typeof value !== "string") return null;
  const cleaned = value
    .replace(/<\s*(script|style)[^>]*>[\s\S]*?<\s*\/\s*\1\s*>/gi, "")
    .replace(/<[^>]*>/g, "")
    // oxlint-disable-next-line no-control-regex -- source metadata must never retain control bytes.
    .replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/g, "")
    .replace(/\r\n?/g, "\n")
    .replace(/[^\S\n]+/g, " ")
    .trim();
  return cleaned && cleaned.length <= maximum ? cleaned : null;
}

function canonicalUrl(value: unknown): string | null {
  try {
    const parsed = new URL(value as string);
    if (!["http:", "https:"].includes(parsed.protocol)) return null;
    parsed.username = "";
    parsed.password = "";
    parsed.search = "";
    parsed.hash = "";
    return parsed.toString();
  } catch {
    return null;
  }
}

function sourceItemId(provider: keyof typeof PROVIDER_CODES, pageUrl: string): string | null {
  const pathname = new URL(pageUrl).pathname;
  if (provider === "Printables") return pathname.match(/\/model\/(\d+)/)?.[1] || null;
  if (provider === "MakerWorld") return pathname.match(/\/models\/(\d+)/)?.[1] || null;
  if (provider === "Thingiverse") return pathname.match(/\/(?:thing:|things\/)(\d+)/)?.[1] || null;
  return pathname.match(/\/3d-model\/[^/]+\/([^/]+)/)?.[1] || null;
}

function deterministicDigest(value: string): string {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36);
}

function stableIdentity(value: string): string {
  const safe = value.replace(/[^a-zA-Z0-9._-]/g, "_").replace(/^_+|_+$/g, "");
  return safe && safe.length <= 170 ? safe : `file-${deterministicDigest(value)}`;
}

export function stableCaptureFileId(
  providerItemId: string | null,
  filename: string,
  providerFileIdentity = filename,
): string {
  const prefix = (providerItemId || "source").replace(/[^a-zA-Z0-9._-]/g, "_").slice(0, 80);
  if (!filename) throw new Error("Capture filename cannot produce a stable file ID.");
  return `${prefix}:${stableIdentity(providerFileIdentity)}`;
}

function jsonLdObjects(jsonLd: string[]): JsonRecord[] {
  if (jsonLd.length > JSON_LD_MAX_SCRIPTS) return [];
  const encoder = new TextEncoder();
  let totalBytes = 0;
  for (const script of jsonLd) {
    const scriptBytes = encoder.encode(script).byteLength;
    if (
      scriptBytes > JSON_LD_MAX_SCRIPT_BYTES ||
      totalBytes + scriptBytes > JSON_LD_MAX_TOTAL_BYTES
    ) {
      return [];
    }
    totalBytes += scriptBytes;
  }

  const objects: JsonRecord[] = [];
  let nodeCount = 0;
  let objectCount = 0;
  for (const script of jsonLd) {
    try {
      const parsed = JSON.parse(script);
      const queue: Array<{ value: unknown; depth: number }> = [{ value: parsed, depth: 0 }];
      const enqueue = (value: unknown, depth: number): boolean => {
        if (depth > JSON_LD_MAX_DEPTH || queue.length >= JSON_LD_MAX_QUEUE) return false;
        queue.push({ value, depth });
        return true;
      };
      while (queue.length) {
        const entry = queue.shift();
        if (!entry) return [];
        nodeCount += 1;
        if (nodeCount > JSON_LD_MAX_NODES) return [];
        const current = entry.value;
        if (!current || typeof current !== "object") continue;
        if (Array.isArray(current)) {
          for (const value of current) {
            if (!enqueue(value, entry.depth + 1)) return [];
          }
          continue;
        }
        objectCount += 1;
        if (objectCount > JSON_LD_MAX_OBJECTS) return [];
        objects.push(current as JsonRecord);
        for (const value of Object.values(current)) {
          if (value && typeof value === "object" && !enqueue(value, entry.depth + 1)) {
            return [];
          }
        }
      }
    } catch {
      // Invalid page JSON-LD is ignored; it is never forwarded.
    }
  }
  return objects;
}

function firstText(objects: JsonRecord[], keys: string[], maximum: number): string | null {
  for (const object of objects) {
    for (const key of keys) {
      const value = plainText(object[key], maximum);
      if (value) return value;
    }
  }
  return null;
}

function person(objects: JsonRecord[]): {
  name?: string | null;
  id?: string | null;
  url?: string | null;
} {
  for (const object of objects) {
    for (const key of ["creator", "author"]) {
      const value = object[key];
      if (typeof value === "string") return { name: plainText(value, FIELD_LIMITS.creator_name) };
      if (value && typeof value === "object" && !Array.isArray(value)) {
        const record = value as JsonRecord;
        return {
          name: plainText(record.name, FIELD_LIMITS.creator_name),
          id: plainText(record.identifier, FIELD_LIMITS.creator_id),
          url: canonicalUrl(record.url),
        };
      }
    }
  }
  return {};
}

function add(
  fields: Record<string, CaptureSourceField>,
  name: string,
  value: string | null | undefined,
  origin: "confirmed" | "inferred" = "confirmed",
) {
  if (value) fields[name] = { value, origin };
}

function normalizedTags(value: unknown): string[] | null {
  const candidates = Array.isArray(value)
    ? value.flatMap((tag) => (Array.isArray(tag) ? tag : [tag]))
    : typeof value === "string"
      ? value.split(",")
      : [];
  const tags = [
    ...new Set(
      candidates
        .map((tag) => plainText(tag, 255)?.toLowerCase())
        .filter((tag): tag is string => Boolean(tag)),
    ),
  ];
  return tags.length > 0 && tags.length <= 100 ? tags : null;
}

function isoDateTime(value: unknown): string | null {
  if (typeof value !== "string" || value.length > FIELD_LIMITS.published_at) return null;
  if (!/^\d{4}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2}))?$/.test(value))
    return null;
  return Number.isNaN(Date.parse(value)) ? null : value;
}

/**
 * Create the V2-compatible source portion of a browser capture message.
 * It deliberately holds no file bytes, provider session material, raw HTML,
 * JSON-LD scripts, or signed download URLs.
 */
export interface CaptureSourceTextField {
  value: string;
  origin: "confirmed" | "inferred";
}

export type CaptureSourceField = CaptureSourceTextField;

export interface CaptureSourceDraft {
  provider: "printables" | "makerworld" | "thingiverse" | "cults";
  canonical_url: string;
  source_item_id: string | null;
  source_revision: string | null;
  adapter_version: string;
  tags: string[];
  fields: Record<string, CaptureSourceField>;
}

export interface BrowserSourceMetadata {
  title?: string;
  description?: string;
  instructions?: string;
  creatorName?: string;
  creatorId?: string;
  creatorUrl?: string;
  tags?: string[];
  publishedAt?: string;
  updatedAt?: string;
  licenseCode?: string;
  licenseUrl?: string;
  licenseText?: string;
}

export interface BrowserCaptureMessage {
  schema_version: 2;
  kind: "browser_source";
  state: "ready" | "manual_file_required";
  message?: string;
  manual_file?: { mapping: "user_selected_file"; source_item_id: string | null };
  source: CaptureSourceDraft;
  candidates: Array<{
    id: string;
    filename: string;
    fileType: "stl" | "gcode" | "sla" | "other";
    mediaType?: string;
    sizeBytes?: number;
  }>;
}

export function buildBrowserCaptureMessage({
  provider,
  pageUrl,
  pageTitle,
  jsonLd = [],
  sourceMetadata,
}: {
  provider: keyof typeof PROVIDER_CODES;
  pageUrl: string;
  pageTitle?: string;
  jsonLd?: string[];
  sourceMetadata?: BrowserSourceMetadata;
}): BrowserCaptureMessage {
  const providerCode = PROVIDER_CODES[provider] as CaptureSourceDraft["provider"];
  if (!providerCode) throw new Error("Capture provider is not supported.");
  const canonical = canonicalUrl(pageUrl);
  if (!canonical) throw new Error("Capture page URL must be an absolute HTTP(S) URL.");
  const objects = jsonLdObjects(jsonLd);
  const fields: Record<string, CaptureSourceField> = {};
  const title =
    plainText(sourceMetadata?.title, FIELD_LIMITS.title) ||
    firstText(objects, ["name", "headline"], FIELD_LIMITS.title);
  add(
    fields,
    "title",
    title || plainText(pageTitle, FIELD_LIMITS.title),
    title ? "confirmed" : "inferred",
  );
  add(
    fields,
    "description",
    plainText(sourceMetadata?.description, FIELD_LIMITS.description) ||
      firstText(objects, ["description"], FIELD_LIMITS.description),
  );
  add(
    fields,
    "instructions",
    plainText(sourceMetadata?.instructions, FIELD_LIMITS.instructions) ||
      firstText(objects, ["instructions"], FIELD_LIMITS.instructions),
  );

  const author = person(objects);
  add(
    fields,
    "creator_name",
    plainText(sourceMetadata?.creatorName, FIELD_LIMITS.creator_name) || author.name,
  );
  add(
    fields,
    "creator_id",
    plainText(sourceMetadata?.creatorId, FIELD_LIMITS.creator_id) || author.id,
  );
  add(fields, "creator_url", canonicalUrl(sourceMetadata?.creatorUrl) || author.url);

  const tags =
    normalizedTags(sourceMetadata?.tags) ||
    normalizedTags(objects.flatMap((object) => [object.keywords, object.tags])) ||
    [];
  const publishedAt =
    isoDateTime(sourceMetadata?.publishedAt) ||
    firstText(objects, ["datePublished", "dateCreated"], FIELD_LIMITS.published_at);
  const updatedAt =
    isoDateTime(sourceMetadata?.updatedAt) ||
    firstText(objects, ["dateModified", "dateUpdated"], FIELD_LIMITS.updated_at);
  add(fields, "published_at", isoDateTime(publishedAt));
  add(fields, "updated_at", isoDateTime(updatedAt));

  const license = firstText(objects, ["license"], FIELD_LIMITS.license_text);
  add(fields, "license_url", canonicalUrl(sourceMetadata?.licenseUrl));
  add(fields, "license_code", plainText(sourceMetadata?.licenseCode, FIELD_LIMITS.license_code));
  add(fields, "license_text", plainText(sourceMetadata?.licenseText, FIELD_LIMITS.license_text));
  if (
    !sourceMetadata?.licenseUrl &&
    !sourceMetadata?.licenseCode &&
    !sourceMetadata?.licenseText &&
    license
  ) {
    const licenseUrl = canonicalUrl(license);
    if (licenseUrl) add(fields, "license_url", licenseUrl);
    else add(fields, "license_code", license);
  }
  const candidates: BrowserCaptureMessage["candidates"] = [];
  const manualFileRequired =
    providerCode === "thingiverse" || providerCode === "cults" || providerCode === "printables";

  return {
    schema_version: 2,
    kind: "browser_source",
    candidates,
    state: manualFileRequired ? "manual_file_required" : "ready",
    ...(manualFileRequired
      ? {
          message:
            providerCode === "thingiverse"
              ? "Choose a downloaded Thingiverse file to attach it to this metadata draft."
              : providerCode === "cults"
                ? "Choose a downloaded Cults file to attach it to this metadata draft."
                : "Choose a downloaded Printables file to attach it in Pending Imports.",
          manual_file: {
            mapping: "user_selected_file",
            source_item_id: sourceItemId(provider, canonical),
          },
        }
      : {}),
    source: {
      provider: providerCode,
      canonical_url: canonical,
      source_item_id: sourceItemId(provider, canonical),
      source_revision: null,
      adapter_version: ADAPTER_VERSION,
      tags,
      fields,
    },
  };
}
