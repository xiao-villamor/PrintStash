import { buildBrowserCaptureMessage, type BrowserCaptureMessage } from "./capture-adapter.ts";
import type { BrowserCaptureFile } from "./capture-transport.ts";

export const THINGIVERSE_MAX_FILE_SIZE_BYTES = 512 * 1024 * 1024;

const THINGIVERSE_DOWNLOAD_HOSTS = new Set([
  "thingiverse.com",
  "www.thingiverse.com",
  "api.thingiverse.com",
  "cdn.thingiverse.com",
]);
const THINGIVERSE_PERMISSION_ORIGINS = [
  "https://thingiverse.com/*",
  "https://www.thingiverse.com/*",
  "https://cdn.thingiverse.com/*",
  "https://api.thingiverse.com/*",
];

export type ThingiverseFileType = "stl" | "gcode" | "sla" | "other";

export interface ThingiversePageFile {
  id: string;
  filename: string;
  fileType: ThingiverseFileType;
  url: string;
}

export interface ThingiverseFilesPageResult {
  ok: boolean;
  files?: ThingiversePageFile[];
  code?: "contract_changed";
}

function candidateId(sourceItemId: string, fileId: string): string {
  return `thingiverse:${sourceItemId}:file:${fileId}`;
}

function isSafeThingiverseUrl(value: string): boolean {
  try {
    const parsed = new URL(value);
    return (
      parsed.protocol === "https:" &&
      !parsed.username &&
      !parsed.password &&
      !parsed.hash &&
      THINGIVERSE_DOWNLOAD_HOSTS.has(parsed.hostname.toLowerCase())
    );
  } catch {
    return false;
  }
}

function validDownloadUrl(value: string, fileId: string): boolean {
  if (!isSafeThingiverseUrl(value)) return false;
  const pathname = new URL(value).pathname;
  return (
    pathname === `/download:${fileId}` || new RegExp(`^/files/${fileId}/download/?$`).test(pathname)
  );
}

function validFilename(value: string): boolean {
  return (
    value.length > 0 &&
    value.length <= 255 &&
    value === value.trim() &&
    // oxlint-disable-next-line no-control-regex -- filenames must reject every control byte.
    !/[\\/\0-\x1f\x7f]/.test(value) &&
    !value.startsWith(".") &&
    /\.(?:stl|3mf|obj|gcode|gco|bgcode|ctb|photon|pwmo|step|stp|scad|dxf|svg|amf|ply|zip|rar|7z|pdf|txt)$/i.test(
      value,
    )
  );
}

function fileTypeFor(filename: string): ThingiverseFileType {
  if (/\.stl$/i.test(filename)) return "stl";
  if (/\.(?:gcode|gco|bgcode)$/i.test(filename)) return "gcode";
  if (/\.(?:ctb|photon|pwmo)$/i.test(filename)) return "sla";
  return "other";
}

/** Runs in the page's MAIN world. Keep this function closure-free. */
export function requestThingiverseFilesInMainWorld(args: {
  sourceItemId: string;
}): ThingiverseFilesPageResult {
  const supportedFilename =
    /([^\n<>:"/\\|?*]{1,240}\.(?:stl|3mf|obj|gcode|gco|bgcode|ctb|photon|pwmo|step|stp|scad|dxf|svg|amf|ply|zip|rar|7z|pdf|txt))/i;
  const safeFilename = (value: string | null | undefined): string | undefined => {
    if (!value) return undefined;
    const match = value.trim().match(supportedFilename);
    if (!match) return undefined;
    const filename = match[1].trim();
    // oxlint-disable-next-line no-control-regex -- page filenames must reject every control byte.
    return filename.length <= 255 && !/[\\/\0-\x1f\x7f]/.test(filename) ? filename : undefined;
  };
  const typeFor = (filename: string): ThingiverseFileType => {
    if (/\.stl$/i.test(filename)) return "stl";
    if (/\.(?:gcode|gco|bgcode)$/i.test(filename)) return "gcode";
    if (/\.(?:ctb|photon|pwmo)$/i.test(filename)) return "sla";
    return "other";
  };
  if (!/^\d{1,20}$/.test(args.sourceItemId)) return { ok: false, code: "contract_changed" };
  const files: ThingiversePageFile[] = [];
  const seen = new Set<string>();
  const anchors = Array.from(document.querySelectorAll<HTMLAnchorElement>("a[href]"));
  if (anchors.length > 2_000) return { ok: false, code: "contract_changed" };
  for (const anchor of anchors) {
    let parsed: URL;
    try {
      parsed = new URL(anchor.href, document.baseURI);
    } catch {
      continue;
    }
    const host = parsed.hostname.toLowerCase();
    if (
      parsed.protocol !== "https:" ||
      parsed.username ||
      parsed.password ||
      parsed.hash ||
      ![
        "thingiverse.com",
        "www.thingiverse.com",
        "api.thingiverse.com",
        "cdn.thingiverse.com",
      ].includes(host)
    ) {
      continue;
    }
    const fileId =
      parsed.pathname.match(/^\/download:(\d{1,20})\/?$/)?.[1] ||
      parsed.pathname.match(/^\/files\/(\d{1,20})\/download\/?$/)?.[1];
    if (!fileId || seen.has(fileId)) continue;
    let filename =
      safeFilename(anchor.download) ||
      safeFilename(anchor.title) ||
      safeFilename(anchor.getAttribute("aria-label")) ||
      safeFilename(anchor.textContent);
    let ancestor: Element | null = anchor.parentElement;
    for (let depth = 0; !filename && ancestor && depth < 4; depth += 1) {
      filename = safeFilename(ancestor.textContent);
      ancestor = ancestor.parentElement;
    }
    if (!filename) continue;
    seen.add(fileId);
    files.push({ id: fileId, filename, fileType: typeFor(filename), url: parsed.toString() });
    if (files.length > 256) return { ok: false, code: "contract_changed" };
  }
  return files.length > 0 ? { ok: true, files } : { ok: false, code: "contract_changed" };
}

export function validateThingiverseFiles(result: unknown): ThingiversePageFile[] {
  if (!result || typeof result !== "object" || Array.isArray(result)) {
    throw new Error("Thingiverse file contract changed.");
  }
  const record = result as { ok?: unknown; files?: unknown };
  if (record.ok !== true || !Array.isArray(record.files) || record.files.length > 256) {
    throw new Error("Thingiverse file contract changed.");
  }
  const ids = new Set<string>();
  return record.files.map((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error("Thingiverse file contract changed.");
    }
    const file = value as Record<string, unknown>;
    if (
      typeof file.id !== "string" ||
      !/^\d{1,20}$/.test(file.id) ||
      ids.has(file.id) ||
      typeof file.filename !== "string" ||
      !validFilename(file.filename) ||
      file.fileType !== fileTypeFor(file.filename) ||
      typeof file.url !== "string" ||
      !validDownloadUrl(file.url, file.id)
    ) {
      throw new Error("Thingiverse file contract changed.");
    }
    ids.add(file.id);
    return {
      id: file.id,
      filename: file.filename,
      fileType: file.fileType as ThingiverseFileType,
      url: file.url,
    };
  });
}

export function thingiverseCaptureFromPage({
  pageUrl,
  pageTitle,
  jsonLd,
  files,
}: {
  pageUrl: string;
  pageTitle?: string;
  jsonLd: string[];
  files: readonly ThingiversePageFile[];
}): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "Thingiverse",
    pageUrl,
    pageTitle,
    jsonLd,
  });
  const sourceItemId = capture.source.source_item_id;
  const candidates = sourceItemId
    ? files.map((file) => ({
        id: candidateId(sourceItemId, file.id),
        filename: file.filename,
        fileType: file.fileType,
      }))
    : [];
  return {
    ...capture,
    state: candidates.length > 0 ? "ready" : "manual_file_required",
    candidates,
    ...(candidates.length > 0
      ? {}
      : {
          message:
            "user_file_required: Thingiverse file links could not be read. Download the files normally, then attach one in Pending Imports.",
          manual_file: { mapping: "user_selected_file" as const, source_item_id: sourceItemId },
        }),
  };
}

async function readBoundedFile(response: Response, signal?: AbortSignal): Promise<Blob> {
  const contentLengthValue = response.headers.get("Content-Length");
  if (contentLengthValue !== null) {
    const contentLength = Number(contentLengthValue);
    if (
      !Number.isSafeInteger(contentLength) ||
      contentLength < 0 ||
      contentLength > THINGIVERSE_MAX_FILE_SIZE_BYTES
    ) {
      throw new Error(
        "user_file_required: The selected Thingiverse file is too large. Download it normally, then attach it in Pending Imports.",
      );
    }
  }
  if (!response.body) {
    throw new Error(
      "user_file_required: Thingiverse did not provide a file stream. Download it normally, then attach it in Pending Imports.",
    );
  }
  const reader = response.body.getReader();
  const chunks: ArrayBuffer[] = [];
  let total = 0;
  const abortReader = () => void reader.cancel();
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
      if (total > THINGIVERSE_MAX_FILE_SIZE_BYTES) {
        await reader.cancel();
        throw new Error(
          "user_file_required: The selected Thingiverse file is too large. Download it normally, then attach it in Pending Imports.",
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
  return new Blob(chunks, {
    type: response.headers.get("Content-Type") || "application/octet-stream",
  });
}

export async function downloadThingiverseCandidate({
  fetchImpl = fetch,
  candidate,
  link,
  ensureOriginPermissions = async () => {},
  signal,
}: {
  fetchImpl?: typeof fetch;
  candidate: BrowserCaptureMessage["candidates"][number];
  link: string;
  ensureOriginPermissions?: (origins: string[]) => Promise<void>;
  signal?: AbortSignal;
}): Promise<BrowserCaptureFile> {
  const fileId = candidate.id.match(/:file:(\d{1,20})$/)?.[1];
  if (!fileId || !validFilename(candidate.filename) || !validDownloadUrl(link, fileId)) {
    throw new Error(
      "user_file_required: Thingiverse file mapping changed. Download the file normally, then attach it in Pending Imports.",
    );
  }
  await ensureOriginPermissions(THINGIVERSE_PERMISSION_ORIGINS);
  const response = await fetchImpl(link, {
    credentials: "include",
    cache: "no-store",
    signal,
  });
  if (response.status === 401 || response.status === 403 || response.status === 429) {
    throw new Error(
      "user_file_required: Thingiverse requires a browser check. Complete it in this tab, then try again, or attach the file in Pending Imports.",
    );
  }
  if (!response.ok) {
    throw new Error(
      "user_file_required: Thingiverse could not provide the selected file. Download it normally, then attach it in Pending Imports.",
    );
  }
  if (response.url && !isSafeThingiverseUrl(response.url)) {
    throw new Error(
      "user_file_required: Thingiverse redirected the file to an unsafe host. Attach it manually in Pending Imports.",
    );
  }
  const contentType = (response.headers.get("Content-Type") || "").toLowerCase();
  if (contentType.includes("text/html")) {
    throw new Error(
      "user_file_required: Thingiverse requires a browser check. Complete it in this tab, then try again, or attach the file in Pending Imports.",
    );
  }
  return {
    id: candidate.id,
    file: await readBoundedFile(response, signal),
    filename: candidate.filename,
    mediaType:
      candidate.fileType === "stl"
        ? "model/stl"
        : candidate.fileType === "gcode"
          ? "text/plain"
          : contentType || "application/octet-stream",
  };
}
