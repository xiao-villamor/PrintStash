/**
 * The bounded MakerWorld browser boundary.
 *
 * The design endpoint is read before selection and is reduced to a small,
 * versioned DTO. Temporary package links are resolved only after the user
 * confirms the selected package candidates. Neither MAIN-world function
 * returns raw provider responses, cookies, or signed-link payloads.
 */

import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import { readBoundedMetadataResponse } from "./capture-adapter.ts";
import type { BrowserCaptureFile } from "./capture-transport.ts";

export const MAKERWORLD_METADATA_FIXTURE_VERSION = "makerworld-design-service-v1";
export const MAKERWORLD_METADATA_ADAPTER_VERSION = "makerworld-design-service-v1";
export const MAKERWORLD_MAX_RESPONSE_BYTES = 512 * 1024;
export const MAKERWORLD_MAX_FILES = 64;
export const MAKERWORLD_MAX_FILE_NAME_BYTES = 255;
export const MAKERWORLD_MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024;
export const MAKERWORLD_MAX_TOTAL_SIZE_BYTES = 1024 * 1024 * 1024;

export type MakerWorldFailureCode =
  | "auth_required"
  | "challenge"
  | "cors_failure"
  | "contract_changed"
  | "response_too_large"
  | "response_too_deep"
  | "too_many_files"
  | "request_failed";

export interface MakerWorldPackageFile {
  id: string;
  filename: string;
  fileType: "other";
  sizeBytes?: number;
}

export interface MakerWorldMetadataResponse {
  fixtureVersion: typeof MAKERWORLD_METADATA_FIXTURE_VERSION;
  sourceItemId: string;
  source: BrowserSourceMetadata;
  files: MakerWorldPackageFile[];
}

export interface MakerWorldMetadataPageResult {
  ok: boolean;
  metadata?: MakerWorldMetadataResponse;
  code?: MakerWorldFailureCode;
}

export interface MakerWorldResolvedLink {
  id: string;
  url: string;
}

export function selectMakerWorldCandidates(
  files: readonly MakerWorldPackageFile[],
  selectedIds: readonly string[],
): MakerWorldPackageFile[] {
  if (selectedIds.length > files.length || new Set(selectedIds).size !== selectedIds.length) {
    throw new Error("MakerWorld selection contains duplicate or unknown package IDs.");
  }
  const byId = new Map(files.map((file) => [file.id, file]));
  return selectedIds.map((id) => {
    const file = byId.get(id);
    if (!file) throw new Error("MakerWorld selection contains duplicate or unknown package IDs.");
    return { ...file };
  });
}

export function makerWorldCaptureFromMetadata(
  metadata: MakerWorldMetadataResponse,
  pageUrl: string,
  pageTitle?: string,
): BrowserCaptureMessage {
  const base = buildBrowserCaptureMessage({
    provider: "MakerWorld",
    pageUrl,
    pageTitle,
    sourceMetadata: metadata.source,
  });
  const candidates = metadata.files.map((file) => ({
    id: file.id,
    filename: file.filename,
    fileType: "other" as const,
    ...(file.sizeBytes === undefined ? {} : { sizeBytes: file.sizeBytes }),
  }));
  return {
    ...base,
    source: {
      ...base.source,
      adapter_version: MAKERWORLD_METADATA_ADAPTER_VERSION,
      source_item_id: metadata.sourceItemId,
    },
    state: candidates.length > 0 ? "ready" : "manual_file_required",
    candidates,
    ...(candidates.length > 0
      ? {}
      : {
          message: makerWorldFailureMessage("contract_changed"),
          manual_file: { mapping: "user_selected_file", source_item_id: metadata.sourceItemId },
        }),
  };
}

export const MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS = [
  "makerworld.com",
  "www.makerworld.com",
  "makerworld.bblmw.com",
] as const;

export function isAllowedMakerWorldDownloadUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "https:" &&
      !parsed.username &&
      !parsed.password &&
      !parsed.hash &&
      (MAKERWORLD_ALLOWED_DOWNLOAD_HOSTS as readonly string[]).includes(
        parsed.hostname.toLowerCase(),
      )
    );
  } catch {
    return false;
  }
}

function safeMakerWorldDownloadUrl(value: unknown): string | undefined {
  return typeof value === "string" && isAllowedMakerWorldDownloadUrl(value) ? value : undefined;
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

function boundedFilename(value: unknown): string | undefined {
  const normalized = boundedText(value, MAKERWORLD_MAX_FILE_NAME_BYTES);
  if (!normalized || normalized === "." || normalized === "..") return undefined;
  if (new TextEncoder().encode(normalized).byteLength > MAKERWORLD_MAX_FILE_NAME_BYTES)
    return undefined;
  if (normalized.includes("/") || normalized.includes("\\")) return undefined;
  return normalized;
}

function boundedId(value: unknown): string | undefined {
  if (typeof value !== "string" && typeof value !== "number") return undefined;
  const normalized = String(value);
  return /^[a-zA-Z0-9._:-]{1,255}$/.test(normalized) ? normalized : undefined;
}

function boundedSize(value: unknown): number | undefined {
  const size = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isSafeInteger(size) && size >= 0 && size <= MAKERWORLD_MAX_FILE_SIZE_BYTES
    ? size
    : undefined;
}

function record(value: unknown): { [key: string]: unknown } | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as { [key: string]: unknown })
    : null;
}

function firstText(object: { [key: string]: unknown }, keys: string[], maximum: number) {
  for (const key of keys) {
    const text = boundedText(object[key], maximum);
    if (text) return text;
  }
  return undefined;
}

function sourceFromDesign(design: { [key: string]: unknown }): BrowserSourceMetadata {
  const person = record(design.user) ?? record(design.creator);
  const licenseValue = design.license;
  const license = record(licenseValue);
  const source: BrowserSourceMetadata = {};
  const title = firstText(design, ["name", "title"], 512);
  const description = boundedText(design.description, 64 * 1024);
  const instructions = boundedText(design.instructions, 128 * 1024);
  const creator = record(design.designCreator);
  const creatorName = creator
    ? (firstText(creator, ["name", "handle"], 512) ??
      (person ? firstText(person, ["name", "username"], 512) : undefined))
    : (boundedText(design.designCreator, 512) ??
      (person ? firstText(person, ["name", "username"], 512) : undefined));
  const creatorId = creator
    ? (boundedId(creator.uid ?? creator.id) ?? (person ? boundedId(person.id) : undefined))
    : person
      ? boundedId(person.id)
      : undefined;
  const creatorUrl = person ? safeMetadataUrl(person.url ?? person.profileUrl) : undefined;
  const licenseCode =
    boundedText(licenseValue, 255) ??
    (license ? firstText(license, ["code", "slug", "name"], 255) : undefined);
  const licenseText = license
    ? boundedText(license.description ?? license.text, 64 * 1024)
    : undefined;
  if (title) source.title = title;
  if (description) source.description = description;
  if (instructions) source.instructions = instructions;
  if (creatorName) source.creatorName = creatorName;
  if (creatorId) source.creatorId = creatorId;
  if (creatorUrl) source.creatorUrl = creatorUrl;
  if (licenseCode) source.licenseCode = licenseCode;
  if (licenseText) source.licenseText = licenseText;
  return source;
}

function safeMetadataUrl(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  try {
    const parsed = new URL(value);
    if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash)
      return undefined;
    parsed.search = "";
    return boundedText(parsed.toString(), 2048);
  } catch {
    return undefined;
  }
}

function filenameForInstance(instance: { [key: string]: unknown }, index: number): string {
  const explicit = instance.filename ?? instance.fileName;
  if (explicit !== undefined && explicit !== null) {
    const filename = boundedFilename(explicit);
    if (!filename) throw new Error("MakerWorld package filename changed.");
    return filename;
  }
  const supplied = firstText(instance, ["name", "title"], 255);
  if (!supplied) return `${index + 1}.3mf`;
  const filename = /\.[a-z0-9]{1,8}$/i.test(supplied) ? supplied : `${supplied}.3mf`;
  return boundedFilename(filename) || `${index + 1}.3mf`;
}

function filesFromDesign(
  design: { [key: string]: unknown },
  sourceItemId: string,
): MakerWorldPackageFile[] {
  const rawInstances = design.instances;
  if (rawInstances !== undefined && !Array.isArray(rawInstances)) {
    throw new Error("MakerWorld package response changed.");
  }
  const instances = Array.isArray(rawInstances) ? rawInstances : [];
  if (instances.length > MAKERWORLD_MAX_FILES) throw new Error("MakerWorld has too many packages.");
  const rawDefaultId = design.defaultInstanceId;
  const defaultId =
    rawDefaultId === undefined || rawDefaultId === null ? undefined : boundedId(rawDefaultId);
  if (rawDefaultId !== undefined && rawDefaultId !== null && !defaultId) {
    throw new Error("MakerWorld model identity changed.");
  }
  const entries = instances.length > 0 ? instances : defaultId ? [{ id: defaultId }] : [];
  const files: MakerWorldPackageFile[] = [];
  const ids = new Set<string>();
  for (const [index, entry] of entries.entries()) {
    const instance = record(entry);
    if (!instance) throw new Error("MakerWorld package response changed.");
    const id = boundedId(instance.id);
    if (!id || ids.has(id)) throw new Error("MakerWorld package response changed.");
    const sizeValue = instance.fileSize ?? instance.sizeBytes ?? instance.size;
    const sizeBytes = boundedSize(sizeValue);
    if (sizeValue !== undefined && sizeValue !== null && sizeBytes === undefined) {
      throw new Error("MakerWorld package size changed.");
    }
    ids.add(id);
    files.push({
      id,
      filename: filenameForInstance(instance, index),
      fileType: "other",
      ...(sizeBytes === undefined ? {} : { sizeBytes }),
    });
  }
  if (files.length === 0) throw new Error(`MakerWorld model ${sourceItemId} has no packages.`);
  return files;
}

export function parseMakerWorldMetadataResponse(
  value: unknown,
  expectedSourceItemId: string,
): MakerWorldMetadataResponse {
  const root = record(value);
  const data = root ? (root.data === undefined ? root : record(root.data)) : null;
  if (!data) throw new Error("MakerWorld metadata response changed.");
  const sourceItemId = data.id === undefined ? expectedSourceItemId : boundedId(data.id);
  if (!sourceItemId) throw new Error("MakerWorld model identity changed.");
  if (sourceItemId !== expectedSourceItemId) throw new Error("MakerWorld model identity changed.");
  const files = filesFromDesign(data, expectedSourceItemId);
  return {
    fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
    sourceItemId: expectedSourceItemId,
    source: sourceFromDesign(data),
    files,
  };
}

/** Fetch public MakerWorld metadata from the extension context, without page credentials. */
export async function requestMakerWorldMetadataInExtensionContext({
  fetchImpl = fetch,
  endpoint,
  sourceItemId,
  fixtureVersion,
  maxResponseBytes,
  signal,
}: {
  fetchImpl?: typeof fetch;
  endpoint: string;
  sourceItemId: string;
  fixtureVersion: string;
  maxResponseBytes: number;
  signal?: AbortSignal;
}): Promise<MakerWorldMetadataPageResult> {
  if (fixtureVersion !== MAKERWORLD_METADATA_FIXTURE_VERSION) {
    return { ok: false, code: "contract_changed" };
  }
  let response: Response;
  try {
    response = await fetchImpl(endpoint, {
      credentials: "omit",
      headers: { Accept: "application/json" },
      signal,
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
      metadata: parseMakerWorldMetadataResponse(parsed, sourceItemId),
    };
  } catch {
    return { ok: false, code: "contract_changed" };
  }
}

export function validateMakerWorldMetadataDto(
  value: unknown,
  expectedSourceItemId: string,
): MakerWorldMetadataResponse {
  const dto = record(value);
  if (
    !dto ||
    dto.fixtureVersion !== MAKERWORLD_METADATA_FIXTURE_VERSION ||
    dto.sourceItemId !== expectedSourceItemId ||
    !record(dto.source) ||
    !Array.isArray(dto.files) ||
    dto.files.length === 0 ||
    dto.files.length > MAKERWORLD_MAX_FILES
  ) {
    throw new Error("MakerWorld metadata response changed.");
  }
  const ids = new Set<string>();
  const files: MakerWorldPackageFile[] = [];
  for (const entry of dto.files) {
    const item = record(entry);
    const id = item ? boundedId(item.id) : undefined;
    const filename = item ? boundedFilename(item.filename) : undefined;
    if (!item || !id || !filename || ids.has(id) || item.fileType !== "other") {
      throw new Error("MakerWorld metadata response changed.");
    }
    const sizeBytes = boundedSize(item.sizeBytes);
    if (item.sizeBytes !== undefined && sizeBytes === undefined) {
      throw new Error("MakerWorld metadata response changed.");
    }
    ids.add(id);
    files.push({
      id,
      filename,
      fileType: "other",
      ...(sizeBytes === undefined ? {} : { sizeBytes }),
    });
  }
  const source: BrowserSourceMetadata = {};
  const sourceRecord = record(dto.source);
  if (!sourceRecord) throw new Error("MakerWorld metadata response changed.");
  const sourceLimits: Record<string, number> = {
    title: 512,
    description: 64 * 1024,
    instructions: 128 * 1024,
    creatorName: 512,
    creatorId: 255,
    creatorUrl: 2048,
    licenseCode: 255,
    licenseUrl: 2048,
    licenseText: 64 * 1024,
  };
  for (const [key, maximum] of Object.entries(sourceLimits)) {
    const text = boundedText(sourceRecord[key], maximum);
    if (text) source[key as Exclude<keyof BrowserSourceMetadata, "tags">] = text;
  }
  return {
    fixtureVersion: MAKERWORLD_METADATA_FIXTURE_VERSION,
    sourceItemId: expectedSourceItemId,
    source,
    files,
  };
}

export function validateMakerWorldResolvedLinks(
  selected: readonly MakerWorldPackageFile[],
  value: unknown,
): MakerWorldResolvedLink[] {
  if (!selected.length || !Array.isArray(value))
    throw new Error("MakerWorld link mapping changed.");
  const selectedIds = selected.map((file) => file.id);
  if (new Set(selectedIds).size !== selectedIds.length)
    throw new Error("MakerWorld selection contains duplicate package IDs.");
  const links = new Map<string, string>();
  for (const entry of value) {
    const item = record(entry);
    const id = item ? boundedId(item.id) : undefined;
    const url = item ? safeMakerWorldDownloadUrl(item.url) : undefined;
    if (!id || !url || links.has(id) || !selectedIds.includes(id)) {
      throw new Error("MakerWorld link mapping changed.");
    }
    links.set(id, url);
  }
  if (links.size !== selectedIds.length) throw new Error("MakerWorld link mapping changed.");
  return selectedIds.map((id) => {
    const url = links.get(id);
    if (!url) throw new Error("MakerWorld link mapping changed.");
    return { id, url };
  });
}

export async function readBoundedMakerWorldResponse(
  response: Response,
  expectedSize?: number,
  totalBefore = 0,
  signal?: AbortSignal,
): Promise<Blob> {
  if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  if (
    expectedSize !== undefined &&
    (!Number.isSafeInteger(expectedSize) ||
      expectedSize < 0 ||
      expectedSize > MAKERWORLD_MAX_FILE_SIZE_BYTES ||
      totalBefore + expectedSize > MAKERWORLD_MAX_TOTAL_SIZE_BYTES)
  ) {
    throw new Error(
      "user_file_required: MakerWorld package is too large. Download it normally, then attach it in Pending Imports.",
    );
  }
  const contentLengthHeader = response.headers.get("Content-Length");
  if (contentLengthHeader !== null) {
    const contentLength = Number(contentLengthHeader);
    if (
      !Number.isSafeInteger(contentLength) ||
      contentLength < 0 ||
      contentLength > MAKERWORLD_MAX_FILE_SIZE_BYTES ||
      totalBefore + contentLength > MAKERWORLD_MAX_TOTAL_SIZE_BYTES ||
      (expectedSize !== undefined && expectedSize !== contentLength)
    ) {
      throw new Error(
        "user_file_required: MakerWorld package size changed. Download it normally, then attach it in Pending Imports.",
      );
    }
  }
  if (!response.body)
    throw new Error("user_file_required: MakerWorld did not provide a file stream.");
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
        total > MAKERWORLD_MAX_FILE_SIZE_BYTES ||
        totalBefore + total > MAKERWORLD_MAX_TOTAL_SIZE_BYTES ||
        (expectedSize !== undefined && total > expectedSize)
      ) {
        await reader.cancel();
        throw new Error(
          "user_file_required: MakerWorld package is too large. Download it normally, then attach it in Pending Imports.",
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
      "user_file_required: MakerWorld package size changed. Download it normally, then attach it in Pending Imports.",
    );
  }
  return new Blob(chunks, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

function makerWorldMediaType(filename: string): string {
  if (/\.stl$/i.test(filename)) return "model/stl";
  if (/\.(?:gcode|gco|g|bgcode)$/i.test(filename)) return "text/plain";
  if (/\.3mf$/i.test(filename)) return "model/3mf";
  return "application/octet-stream";
}

export async function downloadMakerWorldCandidate({
  fetchImpl = fetch,
  candidate,
  link,
  totalBefore = 0,
  ensureOriginPermission = async () => {},
  signal,
}: {
  fetchImpl?: typeof fetch;
  candidate: MakerWorldPackageFile;
  link: string;
  totalBefore?: number;
  ensureOriginPermission?: (origin: string) => Promise<void>;
  signal?: AbortSignal;
}): Promise<BrowserCaptureFile> {
  if (!safeMakerWorldDownloadUrl(link)) {
    throw new Error(
      "user_file_required: MakerWorld returned an unsafe download link. Download the package normally, then attach it in Pending Imports.",
    );
  }
  await ensureOriginPermission(`${new URL(link).origin}/*`);
  if (signal?.aborted) throw new DOMException("The operation was aborted.", "AbortError");
  const response = await fetchImpl(link, {
    credentials: "omit",
    cache: "no-store",
    signal,
  });
  if (!response.ok) {
    throw new Error(
      "user_file_required: MakerWorld could not provide the selected package. Download it normally, then attach it in Pending Imports.",
    );
  }
  if (response.url && !safeMakerWorldDownloadUrl(response.url)) {
    throw new Error(
      "user_file_required: MakerWorld redirected the selected package to an unsafe host. Attach it manually in Pending Imports.",
    );
  }
  const file = await readBoundedMakerWorldResponse(
    response,
    candidate.sizeBytes,
    totalBefore,
    signal,
  );
  return {
    id: candidate.id,
    file,
    filename: candidate.filename,
    mediaType: makerWorldMediaType(candidate.filename),
  };
}

/** Injected into the active MakerWorld tab before selection. */
export async function requestMakerWorldMetadataInMainWorld(args: {
  endpoint: string;
  sourceItemId: string;
  fixtureVersion: string;
  maxResponseBytes: number;
}): Promise<MakerWorldMetadataPageResult> {
  const fixtureVersion = "makerworld-design-service-v1";
  const maxResponseBytes = 512 * 1024;
  const maxFiles = 64;
  const asRecord = (value: unknown): { [key: string]: unknown } | null =>
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as { [key: string]: unknown })
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
  const filename = (value: unknown, index: number, explicit = false): string | undefined => {
    const supplied = text(value, 255);
    if (explicit && !supplied) return undefined;
    if (!supplied) return `${index + 1}.3mf`;
    const result = explicit
      ? supplied
      : /\.[a-z0-9]{1,8}$/i.test(supplied)
        ? supplied
        : `${supplied}.3mf`;
    if (
      new TextEncoder().encode(result).byteLength > 255 ||
      result === "." ||
      result === ".." ||
      result.includes("/") ||
      result.includes("\\")
    )
      return explicit ? undefined : `${index + 1}.3mf`;
    return result;
  };
  const size = (value: unknown): number | undefined => {
    const result =
      typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
    return Number.isSafeInteger(result) && result >= 0 && result <= 512 * 1024 * 1024
      ? result
      : undefined;
  };
  const safeUrl = (value: unknown): string | undefined => {
    if (typeof value !== "string") return undefined;
    try {
      const parsed = new URL(value);
      if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.hash)
        return undefined;
      parsed.search = "";
      return parsed.toString();
    } catch {
      return undefined;
    }
  };
  const boundedTextResponse = async (response: Response): Promise<string> => {
    if (!response.body) throw new Error("missing response stream");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let total = 0;
    let body = "";
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        total += next.value.byteLength;
        if (total > maxResponseBytes) throw new Error("response too large");
        body += decoder.decode(next.value, { stream: true });
      }
      return body + decoder.decode();
    } finally {
      reader.releaseLock();
    }
  };
  try {
    if (args.fixtureVersion !== fixtureVersion) return { ok: false, code: "contract_changed" };
    const response = await fetch(args.endpoint, {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (response.status === 401 || response.status === 403)
      return { ok: false, code: "auth_required" };
    if (!response.ok) return { ok: false, code: "request_failed" };
    const parsed: unknown = JSON.parse(await boundedTextResponse(response));
    const root = asRecord(parsed);
    const data = root ? (root.data === undefined ? root : asRecord(root.data)) : null;
    if (!data) return { ok: false, code: "contract_changed" };
    const returnedId = data.id === undefined ? args.sourceItemId : id(data.id);
    if (returnedId !== args.sourceItemId) return { ok: false, code: "contract_changed" };
    if (data.instances !== undefined && !Array.isArray(data.instances))
      return { ok: false, code: "contract_changed" };
    const instances = Array.isArray(data.instances) ? data.instances : [];
    if (instances.length > maxFiles) return { ok: false, code: "too_many_files" };
    const defaultId =
      data.defaultInstanceId === undefined || data.defaultInstanceId === null
        ? undefined
        : id(data.defaultInstanceId);
    if (data.defaultInstanceId !== undefined && data.defaultInstanceId !== null && !defaultId)
      return { ok: false, code: "contract_changed" };
    const entries = instances.length > 0 ? instances : defaultId ? [{ id: defaultId }] : [];
    if (!entries.length) return { ok: false, code: "contract_changed" };
    if (entries.length > maxFiles) return { ok: false, code: "too_many_files" };
    const files: MakerWorldPackageFile[] = [];
    const seen = new Set<string>();
    for (const [index, entry] of entries.entries()) {
      const instance = asRecord(entry);
      const instanceId = instance ? id(instance.id) : undefined;
      const explicitFilename = instance?.filename ?? instance?.fileName;
      const fileName = instance
        ? filename(
            explicitFilename !== undefined && explicitFilename !== null
              ? explicitFilename
              : (instance.name ?? instance.title),
            index,
            explicitFilename !== undefined && explicitFilename !== null,
          )
        : undefined;
      if (!instance || !instanceId || !fileName || seen.has(instanceId))
        return { ok: false, code: "contract_changed" };
      const rawSize = instance.fileSize ?? instance.sizeBytes ?? instance.size;
      const fileSize = size(rawSize);
      if (rawSize !== undefined && rawSize !== null && fileSize === undefined)
        return { ok: false, code: "contract_changed" };
      seen.add(instanceId);
      files.push({
        id: instanceId,
        filename: fileName,
        fileType: "other",
        ...(fileSize === undefined ? {} : { sizeBytes: fileSize }),
      });
    }
    const person = asRecord(data.user) || asRecord(data.creator);
    const licenseValue = data.license;
    const license = asRecord(licenseValue);
    const source: BrowserSourceMetadata = {};
    const title = text(data.name ?? data.title, 512);
    const description = text(data.description, 64 * 1024);
    const instructions = text(data.instructions, 128 * 1024);
    const creator = asRecord(data.designCreator);
    const creatorName = creator
      ? text(creator.name ?? creator.handle, 512) ||
        (person ? text(person.name ?? person.username, 512) : undefined)
      : text(data.designCreator, 512) ||
        (person ? text(person.name ?? person.username, 512) : undefined);
    const creatorId = creator
      ? id(creator.uid ?? creator.id) || (person ? id(person.id) : undefined)
      : person
        ? id(person.id)
        : undefined;
    const creatorUrl = person ? safeUrl(person.url ?? person.profileUrl) : undefined;
    const licenseCode =
      text(licenseValue, 255) ||
      (license ? text(license.code ?? license.slug ?? license.name, 255) : undefined);
    const licenseText = license ? text(license.description ?? license.text, 64 * 1024) : undefined;
    if (title) source.title = title;
    if (description) source.description = description;
    if (instructions) source.instructions = instructions;
    if (creatorName) source.creatorName = creatorName;
    if (creatorId) source.creatorId = creatorId;
    if (creatorUrl) source.creatorUrl = creatorUrl;
    if (licenseCode) source.licenseCode = licenseCode;
    if (licenseText) source.licenseText = licenseText;
    return {
      ok: true,
      metadata: {
        fixtureVersion: fixtureVersion as typeof MAKERWORLD_METADATA_FIXTURE_VERSION,
        sourceItemId: args.sourceItemId,
        source,
        files,
      },
    };
  } catch (error) {
    return {
      ok: false,
      code:
        error instanceof Error && error.message === "response too large"
          ? "response_too_large"
          : "cors_failure",
    };
  }
}

/** Injected into the active MakerWorld tab after selection confirmation. */
export async function requestMakerWorldLinksInMainWorld(args: {
  endpoint: string;
  selectedIds: string[];
  maxResponseBytes: number;
}): Promise<{ ok: boolean; links?: MakerWorldResolvedLink[]; code?: MakerWorldFailureCode }> {
  const maxResponseBytes = 512 * 1024;
  const maxDepth = 10;
  const maxNodes = 4096;
  const maxArrayItems = 256;
  const allowedHosts = ["makerworld.com", "www.makerworld.com", "makerworld.bblmw.com"];
  const id = (value: unknown): string | undefined => {
    if (typeof value !== "string" && typeof value !== "number") return undefined;
    const result = String(value);
    return /^[a-zA-Z0-9._:-]{1,255}$/.test(result) ? result : undefined;
  };
  const safeUrl = (value: unknown): string | undefined => {
    if (typeof value !== "string") return undefined;
    try {
      const parsed = new URL(value);
      return parsed.protocol === "https:" &&
        !parsed.username &&
        !parsed.password &&
        !parsed.hash &&
        allowedHosts.includes(parsed.hostname.toLowerCase())
        ? parsed.toString()
        : undefined;
    } catch {
      return undefined;
    }
  };
  const asRecord = (value: unknown): { [key: string]: unknown } | null =>
    value !== null && typeof value === "object" && !Array.isArray(value)
      ? (value as { [key: string]: unknown })
      : null;
  if (
    !Array.isArray(args.selectedIds) ||
    args.selectedIds.length === 0 ||
    args.selectedIds.length > 64 ||
    new Set(args.selectedIds).size !== args.selectedIds.length ||
    args.selectedIds.some((value) => !id(value))
  ) {
    return { ok: false, code: "contract_changed" };
  }
  try {
    const links: MakerWorldResolvedLink[] = [];
    for (const selectedId of args.selectedIds) {
      const response = await fetch(
        `${args.endpoint.replace(/\/$/, "")}/${encodeURIComponent(selectedId)}/f3mf?type=download`,
        {
          credentials: "include",
          headers: {
            Accept: "*/*",
            "Content-Type": "application/json",
            "X-BBL-App-Source": "makerworld",
            "X-BBL-Client-Name": "MakerWorld",
            "X-BBL-Client-Type": "web",
            "X-BBL-Client-Version": "00.00.00.01",
          },
        },
      );
      if (response.status === 418) return { ok: false, code: "challenge" };
      if (response.status === 401 || response.status === 403)
        return { ok: false, code: "auth_required" };
      if (!response.ok) return { ok: false, code: "request_failed" };
      if (!response.body) return { ok: false, code: "contract_changed" };
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let total = 0;
      let body = "";
      try {
        while (true) {
          const next = await reader.read();
          if (next.done) break;
          total += next.value.byteLength;
          if (total > maxResponseBytes) return { ok: false, code: "response_too_large" };
          body += decoder.decode(next.value, { stream: true });
        }
        body += decoder.decode();
      } finally {
        reader.releaseLock();
      }
      const parsed: unknown = JSON.parse(body);
      const queue: Array<{ value: unknown; depth: number }> = [{ value: parsed, depth: 0 }];
      let nodes = 0;
      while (queue.length) {
        const current = queue.shift();
        if (!current) return { ok: false, code: "response_too_deep" };
        nodes += 1;
        if (nodes > maxNodes || current.depth > maxDepth) {
          return { ok: false, code: "response_too_deep" };
        }
        if (Array.isArray(current.value)) {
          if (current.value.length > maxArrayItems) return { ok: false, code: "response_too_deep" };
          for (const child of current.value) queue.push({ value: child, depth: current.depth + 1 });
        } else {
          const object = asRecord(current.value);
          if (object) {
            for (const child of Object.values(object))
              queue.push({ value: child, depth: current.depth + 1 });
          }
        }
      }
      const root = asRecord(parsed);
      const data = root ? (root.data === undefined ? root : asRecord(root.data)) : null;
      const url = data
        ? safeUrl(data.url ?? data.downloadUrl ?? data.download_url ?? data.link)
        : undefined;
      if (!url) return { ok: false, code: "contract_changed" };
      links.push({ id: selectedId, url });
    }
    return { ok: true, links };
  } catch (error) {
    return {
      ok: false,
      code:
        error instanceof Error && error.message === "response too large"
          ? "response_too_large"
          : "cors_failure",
    };
  }
}

export function makerWorldFailureMessage(code: MakerWorldFailureCode | undefined): string {
  switch (code) {
    case "auth_required":
      return "user_file_required: Sign in to MakerWorld in this tab, or choose a downloaded MakerWorld package to attach it in Pending Imports.";
    case "challenge":
      return "user_file_required: MakerWorld did not authorize the automatic download. Download the selected 3MF from MakerWorld, then attach it below.";
    case "contract_changed":
    case "response_too_large":
    case "response_too_deep":
    case "too_many_files":
      return "user_file_required: MakerWorld changed its package response. Download the package normally, then attach it in Pending Imports.";
    default:
      return "user_file_required: MakerWorld package capture is unavailable. Download the package normally, then attach it in Pending Imports.";
  }
}
