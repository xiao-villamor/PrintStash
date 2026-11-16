import type { ByteProgress } from "./transfer-progress.ts";
import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import { validateCaptureProfile, type CaptureProfile } from "./print-profile.ts";
import { downloadMarketplaceFile, MarketplaceDownloadError } from "./marketplace-download.ts";

export type CrealityCloudPageResult =
  | {
      ok: true;
      kind: "metadata";
      source: BrowserSourceMetadata;
      profiles: Array<{ id: string; filename: string; size: number; profile: CaptureProfile }>;
    }
  | { ok: true; kind: "download"; url: string }
  | {
      ok: false;
      code: "contract_changed" | "sign_in_required" | "download_unavailable" | "request_failed";
    };

/** Self-contained MAIN adapter. Reads only current-publication data and own-site authentication. */
export async function requestCrealityCloudInMainWorld(args: {
  publicationId: string;
  operation: "metadata" | "download";
  profileId?: string;
}): Promise<CrealityCloudPageResult> {
  const invalid = { ok: false, code: "contract_changed" } as const;
  if (
    !["crealitycloud.com", "www.crealitycloud.com"].includes(location.hostname) ||
    location.pathname.match(/^\/(?:[a-z]{2}\/)?model-detail\/([^/]+)\/?$/i)?.[1] !==
      args.publicationId
  )
    return invalid;
  const record = (v: unknown): Record<string, unknown> | null =>
    v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : null;
  const text = (v: unknown, max = 512): v is string =>
    typeof v === "string" && v.trim().length > 0 && v.length <= max && !/[\p{Cc}<>]/u.test(v);
  try {
    const raw = document.getElementById("__NUXT_DATA__")?.textContent;
    if (!raw || new TextEncoder().encode(raw).byteLength > 2 * 1024 * 1024) return invalid;
    const values: unknown = JSON.parse(raw);
    if (!Array.isArray(values) || values.length > 50000) return invalid;
    const value = (r: Record<string, unknown> | null, key: string): unknown => {
      const index = r?.[key];
      return typeof index === "number" &&
        Number.isSafeInteger(index) &&
        index >= 0 &&
        index < values.length
        ? values[index]
        : undefined;
    };
    const models = values
      .map(record)
      .filter(
        (r): r is Record<string, unknown> =>
          r !== null && value(r, "urlAlias") === args.publicationId && text(value(r, "groupName")),
      );
    if (models.length !== 1) return invalid;
    const model = models[0],
      groupId = value(model, "id"),
      title = value(model, "groupName"),
      list = value(model, "model3mfList");
    if (
      !text(groupId, 128) ||
      !text(title) ||
      !Array.isArray(list) ||
      list.length === 0 ||
      list.length > 256
    )
      return invalid;
    const creator = record(value(model, "userInfo"));
    const profiles: Array<{ id: string; filename: string; size: number; profile: CaptureProfile }> =
      [];
    for (const reference of list) {
      if (
        typeof reference !== "number" ||
        !Number.isSafeInteger(reference) ||
        reference < 0 ||
        reference >= values.length
      )
        return invalid;
      const item = record(values[reference]);
      if (!item || value(item, "modelGroupId") !== groupId) return invalid;
      const id = value(item, "id"),
        name = value(item, "name"),
        size = value(item, "size");
      if (
        typeof id !== "string" ||
        !/^[a-f0-9]{24}$/.test(id) ||
        !text(name, 240) ||
        /[\\/]/.test(name) ||
        name.startsWith(".") ||
        typeof size !== "number" ||
        !Number.isSafeInteger(size) ||
        size <= 0 ||
        size > 512 * 1024 * 1024
      )
        return invalid;
      const second = value(item, "secondName"),
        author = record(value(item, "userInfo")),
        authorName = value(author, "nickName"),
        printer = value(item, "printerName");
      const profile: CaptureProfile = {
        id,
        name: text(second) ? second : name,
        origin: "provider",
      };
      if (text(authorName)) profile.author = authorName;
      const creatorId = value(creator, "userId"),
        authorId = value(author, "userId");
      if (
        typeof creatorId === "number" &&
        creatorId > 0 &&
        typeof authorId === "number" &&
        authorId > 0
      )
        profile.creator_relationship = creatorId === authorId ? "model_creator" : "other";
      if (text(printer)) profile.printer = printer;
      for (const [field, key, maximum] of [
        ["estimated_seconds", "printTime", 31622400],
        ["filament_grams", "filamentWeight", 1000000],
        ["plate_count", "plateCount", 1000],
      ] as const) {
        const n = value(item, key);
        if (
          typeof n === "number" &&
          Number.isFinite(n) &&
          n > 0 &&
          n <= maximum &&
          (field !== "plate_count" || Number.isInteger(n))
        )
          profile[field] = n;
      }
      profiles.push({ id, filename: /\.3mf$/i.test(name) ? name : `${name}.3mf`, size, profile });
    }
    if (new Set(profiles.map((p) => p.id)).size !== profiles.length) return invalid;
    if (args.operation === "metadata") {
      const source: BrowserSourceMetadata = { title };
      const creatorName = value(creator, "nickName"),
        license = value(model, "license"),
        description = value(model, "groupDesc");
      if (text(creatorName)) source.creatorName = creatorName;
      if (text(license, 255)) source.licenseCode = license;
      if (typeof description === "string" && description.length <= 65536)
        source.description = description;
      return { ok: true, kind: "metadata", source, profiles };
    }
    if (!profiles.some((p) => p.id === args.profileId)) return invalid;
    const cookie = (name: string) => {
      const item = document.cookie
        .split(";")
        .map((s) => s.trim())
        .find((s) => s.startsWith(name + "="));
      return item ? decodeURIComponent(item.slice(name.length + 1)) : null;
    };
    const token = cookie("model_token"),
      uid = cookie("model_user_id");
    if (!token || token.length > 8192 || /[\p{Cc}]/u.test(token) || !uid || !/^\d{1,20}$/.test(uid))
      return { ok: false, code: "sign_in_required" };
    const response = await fetch("/api/cxy/v3/model/3mfDownload", {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      redirect: "error",
      headers: { "Content-Type": "application/json", __cxy_token_: token, __cxy_uid_: uid },
      body: JSON.stringify({ id: args.profileId }),
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok || !response.body) return { ok: false, code: "request_failed" };
    const reader = response.body.getReader(),
      decoder = new TextDecoder();
    let body = "",
      size = 0;
    try {
      while (true) {
        const next = await reader.read();
        if (next.done) break;
        size += next.value.byteLength;
        if (size > 65536) {
          await reader.cancel();
          return invalid;
        }
        body += decoder.decode(next.value, { stream: true });
      }
    } finally {
      reader.releaseLock();
    }
    const result = record(JSON.parse(body + decoder.decode()));
    if (result?.code !== 0) return { ok: false, code: "download_unavailable" };
    const link = record(result.result)?.downloadUrl;
    if (typeof link !== "string" || link.length > 16384) return invalid;
    const url = new URL(link);
    if (
      url.protocol !== "https:" ||
      url.hostname !== "internal-creality-usa.oss-us-east-1.aliyuncs.com" ||
      url.port ||
      url.username ||
      url.password ||
      url.hash ||
      !url.pathname.endsWith(".3mf")
    )
      return invalid;
    return { ok: true, kind: "download", url: url.href };
  } catch {
    return { ok: false, code: "request_failed" };
  }
}

export function crealityCloudCaptureFromPage(
  pageUrl: string,
  result: CrealityCloudPageResult,
): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "Creality Cloud",
    pageUrl,
    sourceMetadata: result.ok && result.kind === "metadata" ? result.source : undefined,
  });
  if (!result.ok || result.kind !== "metadata") return capture;
  return {
    ...capture,
    state: "ready",
    candidates: result.profiles.map((p) => ({
      id: p.id,
      filename: p.filename,
      fileType: "other",
      sizeBytes: p.size,
      profile: validateCaptureProfile(p.profile, p.id),
    })),
  };
}
export async function downloadCrealityCloudCandidate(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  result: CrealityCloudPageResult;
  ensureOriginPermission(origin: string): Promise<void>;
  signal?: AbortSignal;
  progress?: ByteProgress;
}) {
  if (!options.result.ok || options.result.kind !== "download")
    throw new MarketplaceDownloadError(
      !options.result.ok && options.result.code === "sign_in_required"
        ? "Sign in to Creality Cloud in this tab, then retry."
        : "Creality Cloud did not authorize the selected profile. Refresh the selection and check access on the site.",
    );
  return downloadMarketplaceFile({
    ...options,
    url: options.result.url,
    provider: "Creality Cloud",
    allowedHosts: ["internal-creality-usa.oss-us-east-1.aliyuncs.com"],
  });
}
