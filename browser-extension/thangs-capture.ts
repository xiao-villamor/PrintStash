import { downloadMarketplaceFile } from "./marketplace-download.ts";
import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import type { BrowserCaptureFile } from "./capture-transport.ts";

export class ThangsCaptureError extends Error {}

export type ThangsPageResult =
  | { ok: true; kind: "metadata"; source: BrowserSourceMetadata; filename: string }
  | { ok: true; kind: "download"; url: string; filename: string }
  | {
      ok: false;
      code: "sign_in_required" | "download_unavailable" | "contract_changed" | "request_failed";
    };

/** Runs in MAIN. Only the allowlisted model DTO or temporary file URL leaves this context. */
export async function requestThangsInMainWorld(args: {
  modelId: string;
  operation: "metadata" | "download";
}): Promise<ThangsPageResult> {
  if (
    !/^\d{1,20}$/.test(args.modelId) ||
    !["thangs.com", "www.thangs.com"].includes(location.hostname) ||
    location.pathname.match(/^\/designer\/[^/]+\/3d-model\/(?:[^/]+-)?(\d+)\/?$/)?.[1] !==
      args.modelId
  )
    return { ok: false, code: "contract_changed" };
  const cleanFilename = (name: string) =>
    name.length > 0 && name.length <= 255 && !/[\\/\p{Cc}]/u.test(name) && !name.startsWith(".");
  try {
    if (args.operation === "metadata") {
      const script = document.getElementById("__NEXT_DATA__")?.textContent;
      if (!script || new TextEncoder().encode(script).byteLength > 2 * 1024 * 1024)
        return { ok: false, code: "contract_changed" };
      const page = JSON.parse(script);
      const model = page.props?.pageProps?.fallback?.[`models/v2/${args.modelId}?likes=false`];
      if (
        !model ||
        String(model.id) !== args.modelId ||
        typeof model.name !== "string" ||
        model.name.length > 512 ||
        !Array.isArray(model.parts) ||
        model.parts.length === 0 ||
        model.parts.length > 256
      )
        return { ok: false, code: "contract_changed" };
      const filename = model.parts.length === 1 ? model.parts[0]?.name : `${model.name}.zip`;
      if (
        typeof filename !== "string" ||
        !cleanFilename(filename) ||
        !/\.(stl|obj|3mf|step|stp|zip)$/i.test(filename)
      )
        return { ok: false, code: "contract_changed" };
      const source: BrowserSourceMetadata = { title: model.name };
      if (typeof model.description === "string" && model.description.length <= 65536)
        source.description = model.description;
      if (typeof model.owner?.username === "string" && model.owner.username.length <= 512) {
        source.creatorName = model.owner.username;
        source.creatorUrl = `https://thangs.com/designer/${encodeURIComponent(model.owner.username)}`;
      }
      // model.license is a storage path, not a declaration of legal rights.
      return { ok: true, kind: "metadata", source, filename };
    }
    const token = localStorage.getItem("accessToken");
    if (!token || !/^[A-Za-z0-9._~-]{20,8192}$/.test(token))
      return { ok: false, code: "sign_in_required" };
    const response = await fetch(`/api/v2/models/${args.modelId}/download-url`, {
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      signal: AbortSignal.timeout(30000),
    });
    if (response.status === 401) return { ok: false, code: "sign_in_required" };
    if ([402, 403, 429].includes(response.status))
      return { ok: false, code: "download_unavailable" };
    if (!response.ok || !response.body) return { ok: false, code: "request_failed" };
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let size = 0;
    let body = "";
    try {
      while (true) {
        const part = await reader.read();
        if (part.done) break;
        size += part.value.byteLength;
        if (size > 65536) {
          await reader.cancel();
          return { ok: false, code: "contract_changed" };
        }
        body += decoder.decode(part.value, { stream: true });
      }
    } finally {
      reader.releaseLock();
    }
    const link = JSON.parse(body + decoder.decode());
    if (
      typeof link.signedUrl !== "string" ||
      link.signedUrl.length > 16384 ||
      typeof link.fileName !== "string" ||
      !cleanFilename(link.fileName)
    )
      return { ok: false, code: "contract_changed" };
    const url = new URL(link.signedUrl);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "storage.googleapis.com" ||
      url.port ||
      url.username ||
      url.password ||
      url.hash
    )
      return { ok: false, code: "contract_changed" };
    const filename = decodeURIComponent(url.pathname.split("/").at(-1) ?? "");
    if (!cleanFilename(filename) || !/\.(stl|obj|3mf|step|stp|zip)$/i.test(filename))
      return { ok: false, code: "contract_changed" };
    return { ok: true, kind: "download", url: url.href, filename };
  } catch {
    return { ok: false, code: "request_failed" };
  }
}

export function thangsCaptureFromPage(
  pageUrl: string,
  result: ThangsPageResult,
): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "Thangs",
    pageUrl,
    sourceMetadata: result.ok && result.kind === "metadata" ? result.source : undefined,
  });
  if (!result.ok || result.kind !== "metadata") return capture;
  return {
    ...capture,
    state: "ready",
    candidates: [
      {
        id: `thangs:${capture.source.source_item_id}:package`,
        filename: result.filename,
        fileType: /\.stl$/i.test(result.filename) ? "stl" : "other",
      },
    ],
  };
}

export async function downloadThangsCandidate(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  result: ThangsPageResult;
  ensureOriginPermission(origin: string): Promise<void>;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}): Promise<BrowserCaptureFile> {
  const { result, candidate } = options;
  if (!result.ok)
    throw new ThangsCaptureError(
      result.code === "sign_in_required"
        ? "Sign in to Thangs in this tab, then retry."
        : "Thangs did not authorize this download. Check ownership and your download allowance on the site.",
    );
  if (result.kind !== "download") throw new ThangsCaptureError("Thangs download contract changed.");
  const url = new URL(result.url);
  if (
    url.protocol !== "https:" ||
    url.hostname !== "storage.googleapis.com" ||
    url.port ||
    url.username ||
    url.password ||
    url.hash ||
    result.filename.split(".").at(-1)?.toLowerCase() !==
      candidate.filename.split(".").at(-1)?.toLowerCase()
  )
    throw new ThangsCaptureError(
      "Thangs returned a different file format or download host. Refresh the file selection.",
    );
  return downloadMarketplaceFile({
    ...options,
    url: result.url,
    provider: "Thangs",
    allowedHosts: ["storage.googleapis.com"],
  });
}
