import { readBoundedMetadataResponse } from "./capture-adapter.ts";

/**
 * The Printables browser boundary.
 *
 * The functions in this module are deliberately split in two. Metadata is
 * acquired before selection, while signed links are resolved only after the
 * user confirms a selection. Neither MAIN-world function returns a provider
 * payload; the first returns a bounded metadata DTO and the second returns
 * only the selected link identities and links.
 */

export const PRINTABLES_GRAPHQL_ENDPOINT = "https://api.printables.com/graphql/";
export const PRINTABLES_METADATA_PERMISSION_ORIGIN = "https://api.printables.com/*";
export const PRINTABLES_METADATA_FIXTURE_VERSION = "printables-graphql-metadata-v2";
export const PRINTABLES_METADATA_ADAPTER_VERSION = "printables-graphql-v2";

export const PRINTABLES_METADATA_QUERY = `
query ($id: ID!) {
  print(id: $id) {
    id
    name
    description
    summary
    datePublished
    modified
    user { id publicUsername handle }
    tags { name }
    license { name }
    stls { id name fileSize }
    gcodes { id name fileSize }
    slas { id name fileSize }
    otherFiles { id name fileSize }
  }
}`;

export const PRINTABLES_LINK_MUTATION = `
mutation ($printId: ID!, $source: DownloadSourceEnum!, $fileType: DownloadFileTypeEnum, $id: ID, $files: [DownloadFileInput!]) {
  getDownloadLink(printId: $printId, source: $source, fileType: $fileType, id: $id, files: $files) {
    ok
    output { link files { id link } }
  }
}`;

export const PRINTABLES_MAX_RESPONSE_BYTES = 512 * 1024;
export const PRINTABLES_MAX_FILES = 256;
export const PRINTABLES_MAX_FILE_NAME_BYTES = 255;
export const PRINTABLES_MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024;
const PRINTABLES_MAX_DEPTH = 12;
const PRINTABLES_MAX_NODES = 8_192;
const PRINTABLES_MAX_ARRAY_ITEMS = 512;

export type PrintablesFileType = "stl" | "gcode" | "sla" | "other";

export interface PrintablesMetadataFile {
  id: string;
  filename: string;
  fileType: PrintablesFileType;
  sizeBytes?: number;
}

export interface PrintablesSourceMetadata {
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

export interface PrintablesMetadataResponse {
  fixtureVersion: typeof PRINTABLES_METADATA_FIXTURE_VERSION;
  sourceItemId: string;
  source: PrintablesSourceMetadata;
  files: PrintablesMetadataFile[];
}

export type PrintablesFailureCode =
  | "auth_required"
  | "challenge"
  | "cors_failure"
  | "contract_changed"
  | "response_too_large"
  | "response_too_deep"
  | "too_many_files"
  | "request_failed";

export interface PrintablesMetadataPageResult {
  ok: boolean;
  metadata?: PrintablesMetadataResponse;
  code?: PrintablesFailureCode;
}

export interface PrintablesSelectedFile {
  id: string;
  filename: string;
  fileType: PrintablesFileType;
  sizeBytes?: number;
}

export interface PrintablesResolvedLink {
  id: string;
  url: string;
}

export interface PrintablesLinksPageResult {
  ok: boolean;
  links?: PrintablesResolvedLink[];
  code?: PrintablesFailureCode;
}

export async function readBoundedPrintablesResponse(
  response: Response,
  expectedSize?: number,
  signal?: AbortSignal,
): Promise<Blob> {
  if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  if (
    expectedSize !== undefined &&
    (!Number.isSafeInteger(expectedSize) ||
      expectedSize < 0 ||
      expectedSize > PRINTABLES_MAX_FILE_SIZE_BYTES)
  ) {
    throw new Error(
      "user_file_required: Printables file is too large. Choose a downloaded Printables file to attach it in Pending Imports.",
    );
  }
  const contentLengthHeader = response.headers.get("Content-Length");
  if (contentLengthHeader !== null) {
    const contentLength = Number(contentLengthHeader);
    if (
      !Number.isSafeInteger(contentLength) ||
      contentLength < 0 ||
      contentLength > PRINTABLES_MAX_FILE_SIZE_BYTES ||
      (expectedSize !== undefined && contentLength !== expectedSize)
    ) {
      throw new Error(
        "user_file_required: Printables file size changed. Choose a downloaded Printables file to attach it in Pending Imports.",
      );
    }
  }
  if (!response.body) {
    throw new Error(
      "user_file_required: Printables did not provide a bounded file stream. Choose a downloaded Printables file to attach it in Pending Imports.",
    );
  }
  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let total = 0;
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
      if (
        total > PRINTABLES_MAX_FILE_SIZE_BYTES ||
        (expectedSize !== undefined && total > expectedSize)
      ) {
        await reader.cancel();
        throw new Error(
          "user_file_required: Printables file is too large. Choose a downloaded Printables file to attach it in Pending Imports.",
        );
      }
      const copy = new ArrayBuffer(next.value.byteLength);
      new Uint8Array(copy).set(next.value);
      chunks.push(copy);
    }
  } finally {
    signal?.removeEventListener("abort", abortReader);
    reader.releaseLock();
  }
  if (expectedSize !== undefined && total !== expectedSize) {
    throw new Error(
      "user_file_required: The selected Printables file changed. Choose a downloaded Printables file to attach it in Pending Imports.",
    );
  }
  return new Blob(chunks, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

const FILE_BUCKETS: ReadonlyArray<readonly [string, PrintablesFileType]> = [
  ["stls", "stl"],
  ["gcodes", "gcode"],
  ["slas", "sla"],
  ["otherFiles", "other"],
];

const SOURCE_FIELD_LIMITS = {
  title: 512,
  description: 64 * 1024,
  instructions: 128 * 1024,
  creatorName: 512,
  creatorId: 255,
  creatorUrl: 2048,
  publishedAt: 64,
  updatedAt: 64,
  licenseCode: 255,
  licenseUrl: 2048,
  licenseText: 64 * 1024,
} as const;

type JsonRecord = { [key: string]: JsonValue };
type JsonValue = null | boolean | number | string | JsonValue[] | JsonRecord;

function record(value: JsonValue): JsonRecord | null {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function boundedText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  const normalized = value.normalize("NFC").replace(/\r\n?/g, "\n").trim();
  if (
    !normalized ||
    normalized.length > maximum ||
    // oxlint-disable-next-line no-control-regex -- reject control bytes from provider metadata.
    /[\u0000-\u001F\u007F]/.test(normalized) ||
    /<\s*\/?\s*[a-z][^>]*>/i.test(normalized)
  ) {
    return undefined;
  }
  return normalized;
}

function boundedHtmlText(value: unknown, maximum: number): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  const withoutDangerousBlocks = value.replace(
    /<\s*(script|style)[^>]*>[\s\S]*?(?:<\s*\/\s*\1\s*>|$)/gi,
    "",
  );
  const plain = withoutDangerousBlocks
    .replace(/<\s*br\s*\/?>/gi, "\n")
    .replace(/<\s*\/\s*(p|div|li|h[1-6])\s*>/gi, "\n")
    .replace(/<[^>]*>/g, "")
    .replace(/&nbsp;/gi, " ")
    .replace(/&amp;/gi, "&")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .normalize("NFC")
    .replace(/\r\n?/g, "\n")
    .replace(/[ \t]+/g, " ")
    .replace(/[ \t]*\n[ \t]*/g, "\n")
    .trim();
  return plain &&
    plain.length <= maximum &&
    // oxlint-disable-next-line no-control-regex -- reject control bytes from provider HTML, retaining line breaks.
    !/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(plain)
    ? plain
    : undefined;
}

function boundedTags(value: unknown): string[] | undefined {
  if (!Array.isArray(value) || value.length > 100) return undefined;
  const tags = value
    .map((entry) => {
      const item = record(entry as JsonValue);
      return boundedText(item?.name, 255)?.toLowerCase();
    })
    .filter((tag): tag is string => Boolean(tag));
  const unique = [...new Set(tags)];
  return unique.length > 0 && unique.length <= 100 ? unique : undefined;
}

function profileUrlFromHandle(value: unknown): string | undefined {
  if (typeof value !== "string" || !/^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(value)) {
    return undefined;
  }
  return `https://www.printables.com/@${value}`;
}

function boundedFilename(value: unknown): string | undefined {
  if (typeof value !== "string" || !value) return undefined;
  const normalized = value.normalize("NFC").trim();
  const bytes = new TextEncoder().encode(normalized).byteLength;
  if (
    !normalized ||
    bytes > PRINTABLES_MAX_FILE_NAME_BYTES ||
    normalized === "." ||
    normalized === ".." ||
    normalized.includes("/") ||
    normalized.includes("\\") ||
    // oxlint-disable-next-line no-control-regex -- reject control bytes from provider filenames.
    /[\u0000-\u001F\u007F]/.test(normalized)
  ) {
    return undefined;
  }
  return normalized;
}

function boundedId(value: unknown): string | undefined {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const normalized = String(value);
  return /^[a-zA-Z0-9._:-]{1,255}$/.test(normalized) ? normalized : undefined;
}

function boundedSize(value: unknown): number | undefined {
  const size = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isSafeInteger(size) && size >= 0 && size <= PRINTABLES_MAX_FILE_SIZE_BYTES
    ? size
    : undefined;
}

function boundedUrl(value: JsonValue | undefined): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash) {
      return undefined;
    }
    parsed.search = "";
    return parsed.toString();
  } catch {
    return undefined;
  }
}

function complexityCheck(value: JsonValue): boolean {
  const queue: Array<{ value: JsonValue; depth: number }> = [{ value, depth: 0 }];
  let nodes = 0;
  while (queue.length) {
    const item = queue.shift();
    if (!item) return false;
    nodes += 1;
    if (nodes > PRINTABLES_MAX_NODES || item.depth > PRINTABLES_MAX_DEPTH) return false;
    if (Array.isArray(item.value)) {
      if (item.value.length > PRINTABLES_MAX_ARRAY_ITEMS) return false;
      for (const child of item.value) queue.push({ value: child, depth: item.depth + 1 });
      continue;
    }
    if (item.value && typeof item.value === "object") {
      for (const child of Object.values(item.value)) {
        queue.push({ value: child, depth: item.depth + 1 });
      }
    }
  }
  return true;
}

function sourceFromPrint(printObject: JsonRecord): PrintablesSourceMetadata {
  const person = record(printObject.user);
  const license = record(printObject.license);
  const title = boundedText(printObject.name, SOURCE_FIELD_LIMITS.title);
  const description = boundedHtmlText(printObject.description, SOURCE_FIELD_LIMITS.description);
  const summary = boundedHtmlText(printObject.summary, SOURCE_FIELD_LIMITS.description);
  const creatorName = boundedText(person?.publicUsername, SOURCE_FIELD_LIMITS.creatorName);
  const creatorId = boundedId(person?.id);
  const creatorUrl = profileUrlFromHandle(person?.handle);
  const tags = boundedTags(printObject.tags);
  const publishedAt = boundedText(printObject.datePublished, SOURCE_FIELD_LIMITS.publishedAt);
  const updatedAt = boundedText(printObject.modified, SOURCE_FIELD_LIMITS.updatedAt);
  const licenseCode = boundedText(license?.name, SOURCE_FIELD_LIMITS.licenseCode);
  return {
    ...(title ? { title } : {}),
    ...(description || summary ? { description: description ?? summary } : {}),
    ...(creatorName ? { creatorName } : {}),
    ...(creatorId ? { creatorId } : {}),
    ...(creatorUrl ? { creatorUrl } : {}),
    ...(tags ? { tags } : {}),
    ...(publishedAt ? { publishedAt } : {}),
    ...(updatedAt ? { updatedAt } : {}),
    ...(licenseCode ? { licenseCode } : {}),
  };
}

function filesFromPrint(printObject: JsonRecord): PrintablesMetadataFile[] {
  const files: PrintablesMetadataFile[] = [];
  const ids = new Set<string>();
  for (const [bucket, fileType] of FILE_BUCKETS) {
    const entries = printObject[bucket];
    if (!Array.isArray(entries)) continue;
    for (const entry of entries) {
      const file = record(entry);
      if (!file) throw new Error("invalid Printables file");
      const id = boundedId(file.id);
      const filename = boundedFilename(file.name);
      if (!id || !filename || ids.has(id)) throw new Error("duplicate or invalid Printables file");
      const sizeBytes = boundedSize(file.fileSize);
      if (file.fileSize !== undefined && file.fileSize !== null && sizeBytes === undefined) {
        throw new Error("invalid Printables file size");
      }
      ids.add(id);
      files.push({ id, filename, fileType, ...(sizeBytes === undefined ? {} : { sizeBytes }) });
      if (files.length > PRINTABLES_MAX_FILES) throw new Error("too many Printables files");
    }
  }
  return files;
}

export function parsePrintablesMetadataResponse(
  value: unknown,
  expectedSourceItemId: string,
): PrintablesMetadataResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Printables metadata response changed.");
  }
  const json = value as JsonValue;
  if (!complexityCheck(json)) throw new Error("Printables metadata response is too deep.");
  const root = record(json);
  if (root && root.errors !== undefined) throw new Error("Printables metadata response changed.");
  const data = root ? record(root.data) : null;
  const printObject = data ? record(data.print) : null;
  if (!printObject || boundedId(printObject.id) !== expectedSourceItemId) {
    throw new Error("Printables metadata response changed.");
  }
  const files = filesFromPrint(printObject);
  return {
    fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
    sourceItemId: expectedSourceItemId,
    source: sourceFromPrint(printObject),
    files,
  };
}

/** Fetch public Printables metadata from the extension context, without page credentials. */
export async function requestPrintablesMetadataInExtensionContext({
  fetchImpl = fetch,
  endpoint,
  query,
  sourceItemId,
  fixtureVersion,
  maxResponseBytes,
  signal,
}: {
  fetchImpl?: typeof fetch;
  endpoint: string;
  query: string;
  sourceItemId: string;
  fixtureVersion: string;
  maxResponseBytes: number;
  signal?: AbortSignal;
}): Promise<PrintablesMetadataPageResult> {
  if (fixtureVersion !== PRINTABLES_METADATA_FIXTURE_VERSION) {
    return { ok: false, code: "contract_changed" };
  }
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      credentials: "omit",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      signal,
      body: JSON.stringify({ query, variables: { id: sourceItemId } }),
    });
  } catch {
    return { ok: false, code: "cors_failure" };
  }
  if (response.status === 401 || response.status === 403) {
    return { ok: false, code: "auth_required" };
  }
  if (!response.ok) return { ok: false, code: "request_failed" };
  let body: string;
  try {
    body = await readBoundedMetadataResponse(response, maxResponseBytes, signal);
  } catch (error) {
    if (error instanceof Error && error.message === "response too large") {
      return { ok: false, code: "response_too_large" };
    }
    return { ok: false, code: "cors_failure" };
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return { ok: false, code: "contract_changed" };
  }
  try {
    return {
      ok: true,
      metadata: parsePrintablesMetadataResponse(parsed, sourceItemId),
    };
  } catch (error) {
    if (error instanceof Error && error.message.includes("too deep")) {
      return { ok: false, code: "response_too_deep" };
    }
    return { ok: false, code: "contract_changed" };
  }
}

export function validatePrintablesMetadataDto(
  value: unknown,
  expectedSourceItemId: string,
): PrintablesMetadataResponse {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Printables metadata response changed.");
  }
  const dto = value as {
    fixtureVersion?: unknown;
    sourceItemId?: unknown;
    source?: unknown;
    files?: unknown;
  };
  if (
    dto.fixtureVersion !== PRINTABLES_METADATA_FIXTURE_VERSION ||
    dto.sourceItemId !== expectedSourceItemId ||
    !dto.source ||
    typeof dto.source !== "object" ||
    Array.isArray(dto.source) ||
    !Array.isArray(dto.files) ||
    dto.files.length > PRINTABLES_MAX_FILES
  ) {
    throw new Error("Printables metadata response changed.");
  }
  const files: PrintablesMetadataFile[] = [];
  const ids = new Set<string>();
  for (const value of dto.files) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Printables metadata response changed.");
    }
    const file = value as {
      id?: unknown;
      filename?: unknown;
      fileType?: unknown;
      sizeBytes?: unknown;
    };
    const id = boundedId(file.id);
    const filename = boundedFilename(file.filename);
    if (
      !id ||
      !filename ||
      ids.has(id) ||
      !["stl", "gcode", "sla", "other"].includes(String(file.fileType))
    ) {
      throw new Error("Printables metadata response changed.");
    }
    const sizeBytes = boundedSize(file.sizeBytes);
    if (file.sizeBytes !== undefined && sizeBytes === undefined) {
      throw new Error("Printables metadata response changed.");
    }
    ids.add(id);
    files.push({
      id,
      filename,
      fileType: file.fileType as PrintablesFileType,
      ...(sizeBytes === undefined ? {} : { sizeBytes }),
    });
  }
  const source = dto.source as Record<string, unknown>;
  const sourceMetadata: PrintablesSourceMetadata = {};
  for (const [key, maximum] of Object.entries(SOURCE_FIELD_LIMITS)) {
    const rawValue = source[key];
    const valueAtKey =
      key === "description" ? boundedHtmlText(rawValue, maximum) : boundedText(rawValue, maximum);
    if ((key === "creatorUrl" || key === "licenseUrl") && rawValue !== undefined) {
      const safe = boundedUrl(rawValue as JsonValue);
      if (!safe) throw new Error("Printables metadata response changed.");
      sourceMetadata[key as Exclude<keyof PrintablesSourceMetadata, "tags">] = safe;
      continue;
    }
    if (valueAtKey)
      sourceMetadata[key as Exclude<keyof PrintablesSourceMetadata, "tags">] = valueAtKey;
  }
  if (source.tags !== undefined) {
    const tags = Array.isArray(source.tags)
      ? source.tags
          .map((tag) => boundedText(tag, 255)?.toLowerCase())
          .filter((tag): tag is string => Boolean(tag))
      : [];
    const uniqueTags = [...new Set(tags)];
    if (uniqueTags.length === 0 || uniqueTags.length > 100) {
      throw new Error("Printables metadata response changed.");
    }
    sourceMetadata.tags = uniqueTags;
  }
  return {
    fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
    sourceItemId: expectedSourceItemId,
    source: sourceMetadata,
    files,
  };
}

export function selectedGroups(files: readonly PrintablesSelectedFile[]) {
  const groups: Array<{ fileType: PrintablesFileType; ids: string[] }> = [];
  const byType = new Map<PrintablesFileType, { fileType: PrintablesFileType; ids: string[] }>();
  for (const file of files) {
    let group = byType.get(file.fileType);
    if (!group) {
      group = { fileType: file.fileType, ids: [] };
      byType.set(file.fileType, group);
      groups.push(group);
    }
    group.ids.push(file.id);
  }
  return groups;
}

function printablesDownloadUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const parsed = new URL(value);
    const hostname = parsed.hostname.toLowerCase();
    if (
      parsed.protocol !== "https:" ||
      (hostname !== "printables.com" && !hostname.endsWith(".printables.com")) ||
      parsed.username ||
      parsed.password ||
      parsed.hash
    ) {
      return undefined;
    }
    return parsed.toString();
  } catch {
    return undefined;
  }
}

export function validatePrintablesResolvedLinks(
  selected: readonly PrintablesSelectedFile[],
  value: unknown,
): PrintablesResolvedLink[] {
  if (!selected.length || !Array.isArray(value))
    throw new Error("Printables link mapping changed.");
  const selectedIds = selected.map((file) => file.id);
  if (new Set(selectedIds).size !== selectedIds.length) {
    throw new Error("Printables selection contains duplicate file IDs.");
  }
  const linksById = new Map<string, string>();
  for (const entry of value) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error("Printables link mapping changed.");
    }
    const item = entry as { id?: unknown; link?: unknown };
    const id = boundedId(item.id);
    const link = printablesDownloadUrl(item.link);
    if (!id || !link || linksById.has(id) || !selectedIds.includes(id)) {
      throw new Error("Printables link mapping changed.");
    }
    linksById.set(id, link);
  }
  if (linksById.size !== selectedIds.length) throw new Error("Printables link mapping changed.");
  return selectedIds.map((id) => {
    const url = linksById.get(id);
    if (!url) throw new Error("Printables link mapping changed.");
    return { id, url };
  });
}

function parsePrintablesResolvedLinkPayload(value: unknown): Array<{ id: string; link: string }> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Printables link response changed.");
  }
  const root = value as { data?: unknown; errors?: unknown };
  if (Array.isArray(root.errors) && root.errors.length > 0) {
    throw new Error("Printables link response changed.");
  }
  const data =
    root.data && typeof root.data === "object" && !Array.isArray(root.data)
      ? (root.data as { getDownloadLink?: unknown })
      : null;
  const mutation = data?.getDownloadLink;
  if (!mutation || typeof mutation !== "object" || Array.isArray(mutation)) {
    throw new Error("Printables link response changed.");
  }
  const output = (mutation as { output?: unknown }).output;
  if (!output || typeof output !== "object" || Array.isArray(output)) {
    throw new Error("Printables link response changed.");
  }
  const entries = (output as { files?: unknown }).files;
  if (!Array.isArray(entries) || entries.length > PRINTABLES_MAX_FILES) {
    throw new Error("Printables link response changed.");
  }
  return entries.map((entry) => {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      throw new Error("Printables link response changed.");
    }
    const item = entry as { id?: unknown; fileId?: unknown; link?: unknown };
    const id = boundedId(item.id ?? item.fileId);
    const link = printablesDownloadUrl(item.link);
    if (!id || !link) throw new Error("Printables link response changed.");
    return { id, link };
  });
}

/** Resolve selected Printables links from the extension context after confirmation. */
export async function requestPrintablesLinksInExtensionContext({
  fetchImpl = fetch,
  endpoint,
  query,
  sourceItemId,
  selected,
  maxResponseBytes,
  signal,
}: {
  fetchImpl?: typeof fetch;
  endpoint: string;
  query: string;
  sourceItemId: string;
  selected: readonly PrintablesSelectedFile[];
  maxResponseBytes: number;
  signal?: AbortSignal;
}): Promise<PrintablesLinksPageResult> {
  if (!selected.length) return { ok: false, code: "contract_changed" };
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      method: "POST",
      credentials: "omit",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      signal,
      body: JSON.stringify({
        query,
        variables: {
          printId: sourceItemId,
          source: "model_detail",
          files: selectedGroups(selected),
        },
      }),
    });
  } catch {
    return { ok: false, code: "cors_failure" };
  }
  if (response.status === 401 || response.status === 403) {
    return { ok: false, code: "auth_required" };
  }
  if (!response.ok) return { ok: false, code: "request_failed" };
  let body: string;
  try {
    body = await readBoundedMetadataResponse(response, maxResponseBytes, signal);
  } catch (error) {
    if (error instanceof Error && error.message === "response too large") {
      return { ok: false, code: "response_too_large" };
    }
    return { ok: false, code: "cors_failure" };
  }
  let parsed: JsonValue;
  try {
    parsed = JSON.parse(body) as JsonValue;
  } catch {
    return { ok: false, code: "contract_changed" };
  }
  if (!complexityCheck(parsed)) return { ok: false, code: "response_too_deep" };
  try {
    const links = parsePrintablesResolvedLinkPayload(parsed);
    return { ok: true, links: validatePrintablesResolvedLinks(selected, links) };
  } catch {
    return { ok: false, code: "contract_changed" };
  }
}

/**
 * Injected into the active Printables tab. This seam returns only the typed
 * GraphQL object and stable failure categories, never response text/payload.
 */
export async function requestPrintablesMetadataInMainWorld(args: {
  endpoint: string;
  query: string;
  sourceItemId: string;
  fixtureVersion: string;
  maxResponseBytes: number;
}): Promise<PrintablesMetadataPageResult> {
  const fixtureVersion = "printables-graphql-metadata-v2";
  const maxFiles = 256;
  const maxNameBytes = 255;
  const maxFileSize = 2 * 1024 * 1024 * 1024;
  const maxDepth = 12;
  const maxNodes = 8192;
  const maxArrayItems = 512;
  const sourceLimits: Record<string, number> = {
    title: 512,
    description: 64 * 1024,
    instructions: 128 * 1024,
    creatorName: 512,
    creatorId: 255,
    creatorUrl: 2048,
    publishedAt: 64,
    updatedAt: 64,
    licenseCode: 255,
    licenseUrl: 2048,
    licenseText: 64 * 1024,
  };
  const asRecord = (value: unknown): Record<string, unknown> | null =>
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as Record<string, unknown>)
      : null;
  const id = (value: unknown): string | undefined => {
    if (typeof value !== "string" && typeof value !== "number") return undefined;
    const result = String(value);
    return /^[a-zA-Z0-9._:-]{1,255}$/.test(result) ? result : undefined;
  };
  const text = (value: unknown, maximum: number): string | undefined => {
    if (typeof value !== "string" || !value) return undefined;
    const result = value.normalize("NFC").replace(/\r\n?/g, "\n").trim();
    // oxlint-disable-next-line no-control-regex -- reject control bytes before returning metadata.
    return result && result.length <= maximum && !/[\u0000-\u001F\u007F]/.test(result)
      ? result
      : undefined;
  };
  const htmlText = (value: unknown, maximum: number): string | undefined => {
    if (typeof value !== "string" || !value) return undefined;
    const plain = value
      .replace(/<\s*(script|style)[^>]*>[\s\S]*?(?:<\s*\/\s*\1\s*>|$)/gi, "")
      .replace(/<\s*br\s*\/?>/gi, "\n")
      .replace(/<\s*\/\s*(p|div|li|h[1-6])\s*>/gi, "\n")
      .replace(/<[^>]*>/g, "")
      .replace(/&nbsp;/gi, " ")
      .replace(/&amp;/gi, "&")
      .replace(/&lt;/gi, "<")
      .replace(/&gt;/gi, ">")
      .replace(/&quot;/gi, '"')
      .replace(/&#39;|&apos;/gi, "'")
      .normalize("NFC")
      .replace(/\r\n?/g, "\n")
      .replace(/[ \t]+/g, " ")
      .replace(/[ \t]*\n[ \t]*/g, "\n")
      .trim();
    return plain &&
      plain.length <= maximum &&
      // oxlint-disable-next-line no-control-regex -- reject control bytes before returning metadata, retaining line breaks.
      !/[\u0000-\u0008\u000B\u000C\u000E-\u001F\u007F]/.test(plain)
      ? plain
      : undefined;
  };
  const handleUrl = (value: unknown): string | undefined =>
    typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(value)
      ? `https://www.printables.com/@${value}`
      : undefined;
  const tagList = (value: unknown): string[] | undefined => {
    if (!Array.isArray(value) || value.length > 100) return undefined;
    const tags = value
      .map((entry) => {
        const item = asRecord(entry);
        return text(item?.name, 255)?.toLowerCase();
      })
      .filter((tag): tag is string => Boolean(tag));
    const unique = [...new Set(tags)];
    return unique.length > 0 && unique.length <= 100 ? unique : undefined;
  };
  const filename = (value: unknown): string | undefined => {
    const result = text(value, maxNameBytes);
    if (!result || new TextEncoder().encode(result).byteLength > maxNameBytes) return undefined;
    return result !== "." && result !== ".." && !result.includes("/") && !result.includes("\\")
      ? result
      : undefined;
  };
  const size = (value: unknown): number | undefined => {
    const result =
      typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    return Number.isSafeInteger(result) && result >= 0 && result <= maxFileSize
      ? result
      : undefined;
  };
  const bounded = (value: unknown): boolean => {
    const queue: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }];
    let nodes = 0;
    while (queue.length) {
      const item = queue.shift();
      if (!item || ++nodes > maxNodes || item.depth > maxDepth) return false;
      if (Array.isArray(item.value)) {
        if (item.value.length > maxArrayItems) return false;
        item.value.forEach((child) => queue.push({ value: child, depth: item.depth + 1 }));
      } else if (item.value && typeof item.value === "object") {
        Object.values(item.value).forEach((child) =>
          queue.push({ value: child, depth: item.depth + 1 }),
        );
      }
    }
    return true;
  };
  try {
    if (
      /captcha|verify you are human|access denied/i.test(document.title) ||
      Boolean(document.querySelector('iframe[src*="challenge"], [class*="captcha"]'))
    ) {
      return { ok: false, code: "challenge" };
    }
    const response = await fetch(args.endpoint, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: args.query,
        variables: { id: args.sourceItemId },
      }),
    });
    if (response.status === 401 || response.status === 403)
      return { ok: false, code: "auth_required" };
    if (response.status === 429) return { ok: false, code: "request_failed" };
    if (!response.ok) return { ok: false, code: "request_failed" };
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > args.maxResponseBytes) {
      return { ok: false, code: "response_too_large" };
    }
    const parsed: unknown = JSON.parse(body);
    if (!bounded(parsed)) return { ok: false, code: "response_too_deep" };
    const root = asRecord(parsed);
    const data = root ? asRecord(root.data) : null;
    const print = data ? asRecord(data.print) : null;
    if (!print || id(print.id) !== args.sourceItemId || root?.errors) {
      return { ok: false, code: "contract_changed" };
    }
    const files: Array<{
      id: string;
      filename: string;
      fileType: "stl" | "gcode" | "sla" | "other";
      sizeBytes?: number;
    }> = [];
    const seen = new Set<string>();
    for (const [bucket, fileType] of [
      ["stls", "stl"],
      ["gcodes", "gcode"],
      ["slas", "sla"],
      ["otherFiles", "other"],
    ] as const) {
      const entries = print[bucket];
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        const item = asRecord(entry);
        const fileId = item ? id(item.id) : undefined;
        const fileName = item ? filename(item.name) : undefined;
        if (!fileId || !fileName || seen.has(fileId))
          return { ok: false, code: "contract_changed" };
        const fileSize = size(item?.fileSize);
        if (item?.fileSize !== undefined && item.fileSize !== null && fileSize === undefined)
          return { ok: false, code: "contract_changed" };
        seen.add(fileId);
        files.push({
          id: fileId,
          filename: fileName,
          fileType,
          ...(fileSize === undefined ? {} : { sizeBytes: fileSize }),
        });
        if (files.length > maxFiles) return { ok: false, code: "too_many_files" };
      }
    }
    const person = asRecord(print.user);
    const license = asRecord(print.license);
    const source: PrintablesSourceMetadata = {};
    const sourceValues: Record<string, unknown> = {
      title: print.name,
      description: htmlText(print.description, 64 * 1024) ?? htmlText(print.summary, 64 * 1024),
      creatorName: person?.publicUsername,
      creatorId: person?.id,
      creatorUrl: handleUrl(person?.handle),
      publishedAt: print.datePublished,
      updatedAt: print.modified,
      licenseCode: license?.name,
    };
    for (const [key, maximum] of Object.entries(sourceLimits)) {
      const value =
        key === "description"
          ? htmlText(sourceValues[key], maximum)
          : text(sourceValues[key], maximum);
      if (!value) continue;
      if (key === "title") source.title = value;
      else if (key === "description") source.description = value;
      else if (key === "creatorName") source.creatorName = value;
      else if (key === "creatorId") source.creatorId = value;
      else if (key === "creatorUrl") source.creatorUrl = value;
      else if (key === "publishedAt") source.publishedAt = value;
      else if (key === "updatedAt") source.updatedAt = value;
      else if (key === "licenseCode") source.licenseCode = value;
    }
    const tags = tagList(print.tags);
    if (tags) source.tags = tags;
    const metadata = {
      fixtureVersion: fixtureVersion as typeof PRINTABLES_METADATA_FIXTURE_VERSION,
      sourceItemId: args.sourceItemId,
      source,
      files,
    };
    if (args.fixtureVersion !== fixtureVersion) {
      return { ok: false, code: "contract_changed" };
    }
    return { ok: true, metadata };
  } catch {
    return { ok: false, code: "cors_failure" };
  }
}

/**
 * Injected into the active Printables tab after confirmation. It resolves
 * fresh links with the tab's session and returns no provider payload.
 */
export async function requestPrintablesLinksInMainWorld(args: {
  endpoint: string;
  query: string;
  sourceItemId: string;
  groups: Array<{ fileType: PrintablesFileType; ids: string[] }>;
  maxResponseBytes: number;
}): Promise<{
  ok: boolean;
  links?: Array<{ id: string; link: string }>;
  code?: PrintablesFailureCode;
}> {
  const maxFiles = 256;
  const id = (value: unknown): string | undefined => {
    if (typeof value !== "string" && typeof value !== "number") return undefined;
    const result = String(value);
    return /^[a-zA-Z0-9._:-]{1,255}$/.test(result) ? result : undefined;
  };
  const safeUrl = (value: unknown): string | undefined => {
    if (typeof value !== "string") return undefined;
    try {
      const parsed = new URL(value);
      const hostname = parsed.hostname.toLowerCase();
      if (
        parsed.protocol !== "https:" ||
        (hostname !== "printables.com" && !hostname.endsWith(".printables.com")) ||
        parsed.username ||
        parsed.password ||
        parsed.hash
      ) {
        return undefined;
      }
      return parsed.toString();
    } catch {
      return undefined;
    }
  };
  try {
    const response = await fetch(args.endpoint, {
      method: "POST",
      credentials: "include",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        query: args.query,
        variables: {
          printId: args.sourceItemId,
          source: "model_detail",
          files: args.groups,
        },
      }),
    });
    if (response.status === 401 || response.status === 403)
      return { ok: false, code: "auth_required" };
    if (!response.ok) return { ok: false, code: "request_failed" };
    const body = await response.text();
    if (new TextEncoder().encode(body).byteLength > args.maxResponseBytes) {
      return { ok: false, code: "response_too_large" };
    }
    const parsed: unknown = JSON.parse(body);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      return { ok: false, code: "contract_changed" };
    }
    const root = parsed as { data?: unknown; errors?: unknown };
    if (Array.isArray(root.errors) && root.errors.length > 0) {
      return { ok: false, code: "contract_changed" };
    }
    const data =
      root.data && typeof root.data === "object" && !Array.isArray(root.data)
        ? (root.data as { getDownloadLink?: unknown })
        : null;
    const result = data?.getDownloadLink;
    if (!result || typeof result !== "object" || Array.isArray(result)) {
      return { ok: false, code: "contract_changed" };
    }
    const output = (result as { output?: unknown }).output;
    if (!output || typeof output !== "object" || Array.isArray(output)) {
      return { ok: false, code: "contract_changed" };
    }
    const entries = (output as { files?: unknown }).files;
    if (!Array.isArray(entries) || entries.length > maxFiles) {
      return { ok: false, code: "contract_changed" };
    }
    const links: Array<{ id: string; link: string }> = [];
    for (const entry of entries) {
      if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
        return { ok: false, code: "contract_changed" };
      }
      const item = entry as { id?: unknown; fileId?: unknown; link?: unknown };
      const fileId = id(item.id ?? item.fileId);
      const link = safeUrl(item.link);
      if (!fileId || !link) return { ok: false, code: "contract_changed" };
      links.push({ id: fileId, link });
    }
    return { ok: true, links };
  } catch {
    return { ok: false, code: "cors_failure" };
  }
}

export function printablesFailureMessage(code: PrintablesFailureCode | undefined): string {
  switch (code) {
    case "auth_required":
      return "user_file_required: Sign in to Printables in this tab, or choose a downloaded Printables file to attach it in Pending Imports.";
    case "challenge":
      return "user_file_required: Printables requires a browser check. Choose a downloaded Printables file to attach it in Pending Imports.";
    case "response_too_large":
    case "response_too_deep":
    case "too_many_files":
    case "contract_changed":
      return "user_file_required: Printables changed its file response. Choose a downloaded Printables file to attach it in Pending Imports.";
    default:
      return "user_file_required: Printables file capture is unavailable. Choose a downloaded Printables file to attach it in Pending Imports.";
  }
}
