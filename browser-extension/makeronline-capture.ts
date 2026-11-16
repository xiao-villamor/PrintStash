import {
  buildBrowserCaptureMessage,
  type BrowserCaptureMessage,
  type BrowserSourceMetadata,
} from "./capture-adapter.ts";
import { validateCaptureProfile, type CaptureProfile } from "./print-profile.ts";
import { downloadMarketplaceFile, MarketplaceDownloadError } from "./marketplace-download.ts";

export type MakerOnlinePageResult =
  | {
      ok: true;
      kind: "metadata";
      source: BrowserSourceMetadata;
      profiles: Array<{ id: string; filename: string; size: number; profile: CaptureProfile }>;
    }
  | { ok: true; kind: "download"; url: string }
  | {
      ok: false;
      code:
        | "contract_changed"
        | "verification_required"
        | "download_unavailable"
        | "request_failed";
    };

/** Runs entirely in MAIN: provider authentication and manual verification never leave the site. */
export async function requestMakerOnlineInMainWorld(args: {
  publicationId: string;
  operation: "metadata" | "download";
  profileId?: string;
}): Promise<MakerOnlinePageResult> {
  const invalid = { ok: false, code: "contract_changed" } as const;
  if (
    !["makeronline.com", "www.makeronline.com"].includes(location.hostname) ||
    location.pathname.match(/^\/(?:[a-z]{2}\/)?model\/[^/]+\/(\d+)\.html\/?$/i)?.[1] !==
      args.publicationId
  )
    return invalid;
  const record = (v: unknown): Record<string, unknown> | null =>
    v !== null && typeof v === "object" && !Array.isArray(v)
      ? (v as Record<string, unknown>)
      : null;
  const text = (v: unknown, max = 512): v is string =>
    typeof v === "string" && v.trim().length > 0 && v.length <= max && !/[\p{Cc}<>]/u.test(v);
  type SiteComponent = {
    modelID?: number;
    $children?: SiteComponent[];
    getRequestHeaders?: (risk: boolean, level: number) => Promise<unknown>;
  };
  type SiteRoot = SiteComponent & {
    $store?: { state?: { model?: { detail?: unknown } } };
    $api?: { model?: { downloadFile?: (input: object, headers: unknown) => Promise<unknown> } };
  };
  try {
    const root = (window as unknown as { $nuxt?: SiteRoot }).$nuxt;
    // Use the current SPA publication, never stale SSR data or account state.
    const model = record(root?.$store?.state?.model?.detail);
    if (
      !model ||
      typeof model.id !== "number" ||
      String(model.id) !== args.publicationId ||
      !text(model.title)
    )
      return invalid;
    const list = model.print_profiles;
    if (!Array.isArray(list) || list.length === 0 || list.length > 256) return invalid;
    const profiles: Array<{ id: string; filename: string; size: number; profile: CaptureProfile }> =
      [];
    let selected: Record<string, unknown> | null = null;
    for (const raw of list) {
      const item = record(raw),
        download = record(item?.download);
      if (
        !item ||
        !download ||
        typeof item.id !== "number" ||
        !Number.isSafeInteger(item.id) ||
        item.id <= 0 ||
        download.print_profile_id !== item.id ||
        download.mold_id !== model.id ||
        !text(download.file_name, 240) ||
        /[\\/]/.test(download.file_name) ||
        download.file_name.startsWith(".") ||
        !/\.3mf$/i.test(download.file_name) ||
        !text(item.title) ||
        typeof item.total_size !== "number" ||
        !Number.isSafeInteger(item.total_size) ||
        item.total_size <= 0 ||
        item.total_size > 512 * 1024 * 1024
      )
        return invalid;
      const id = String(item.id);
      const profile: CaptureProfile = { id, name: item.title, origin: "provider" };
      if (text(item.user_display_name)) profile.author = item.user_display_name;
      if (text(model.user_id, 128) && text(item.user_id, 128))
        profile.creator_relationship = model.user_id === item.user_id ? "model_creator" : "other";
      // printer_label lists compatible printers, not the printer used by this profile.
      if (
        typeof item.plate_count === "number" &&
        Number.isInteger(item.plate_count) &&
        item.plate_count > 0 &&
        item.plate_count <= 1000
      )
        profile.plate_count = item.plate_count;
      if (typeof item.print_time_label === "string") {
        const match = /^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?$/.exec(item.print_time_label);
        if (match && item.print_time_label) {
          const seconds =
            Number(match[1] ?? 0) * 3600 + Number(match[2] ?? 0) * 60 + Number(match[3] ?? 0);
          if (seconds > 0 && seconds <= 31622400) profile.estimated_seconds = seconds;
        }
      }
      if (Array.isArray(item.materials_label) && item.materials_label.length <= 64) {
        const materials = item.materials_label.map(record);
        if (materials.every((m) => text(m?.material_type, 128)))
          profile.materials = [...new Set(materials.map((m) => m!.material_type as string))];
        if (
          materials.length &&
          materials.every(
            (m) =>
              typeof m?.filament_used_g === "number" &&
              Number.isFinite(m.filament_used_g) &&
              m.filament_used_g >= 0,
          )
        ) {
          const grams = materials.reduce((sum, m) => sum + (m!.filament_used_g as number), 0);
          if (grams > 0 && grams <= 1000000) profile.filament_grams = grams;
        }
      }
      profiles.push({ id, filename: download.file_name, size: item.total_size, profile });
      if (id === args.profileId) selected = download;
    }
    if (new Set(profiles.map((p) => p.id)).size !== profiles.length) return invalid;
    if (args.operation === "metadata") {
      const source: BrowserSourceMetadata = { title: model.title };
      if (text(model.user_display_name)) source.creatorName = model.user_display_name;
      if (typeof model.desc === "string" && model.desc.length <= 65536)
        source.description = model.desc;
      // Numeric license codes have no verified mapping; retain uncertainty.
      return { ok: true, kind: "metadata", source, profiles };
    }
    if (!selected || !text(selected.key, 128) || !root?.$api?.model?.downloadFile) return invalid;
    const queue: SiteComponent[] = [root];
    const visited = new Set<SiteComponent>();
    let owner: SiteComponent | undefined;
    for (let i = 0; i < queue.length && i < 500; i++) {
      const current = queue[i];
      if (visited.has(current)) continue;
      visited.add(current);
      if (current.modelID === model.id && typeof current.getRequestHeaders === "function") {
        owner = current;
        break;
      }
      if (Array.isArray(current.$children) && queue.length + current.$children.length <= 500)
        queue.push(...current.$children);
    }
    if (!owner?.getRequestHeaders) return invalid;
    const input = {
      key: selected.key,
      file_type: 2,
      is_batch_down: 0,
      print_profile_id: Number(args.profileId),
    };
    let timer: ReturnType<typeof setTimeout> | undefined;
    let expired = false;
    const currentPublication = () =>
      !expired &&
      location.pathname.match(/\/(\d+)\.html\/?$/)?.[1] === args.publicationId &&
      root.$store?.state?.model?.detail === model;
    try {
      const operation = async () => {
        const headers = await owner.getRequestHeaders!(true, 0);
        if (!currentPublication()) return invalid;
        let result = record(await root.$api!.model!.downloadFile!(input, headers));
        if (result?.code === 1003) {
          const level = record(result.data)?.captchaLevel;
          if (typeof level !== "number" || !Number.isInteger(level) || level <= 0 || level > 10)
            return { ok: false, code: "verification_required" } as const;
          // The site's own UI asks the person to verify. No automated CAPTCHA solving.
          const verifiedHeaders = await owner.getRequestHeaders!(true, level);
          if (!currentPublication()) return invalid;
          result = record(await root.$api!.model!.downloadFile!(input, verifiedHeaders));
        }
        if (result?.code === 1003) return { ok: false, code: "verification_required" } as const;
        if (result?.code !== 0) return { ok: false, code: "download_unavailable" } as const;
        if (typeof result.data !== "string" || result.data.length > 16384) return invalid;
        const url = new URL(result.data);
        if (
          url.protocol !== "https:" ||
          url.hostname !== "acop-prod-private.s3-accelerate.amazonaws.com" ||
          url.port ||
          url.username ||
          url.password ||
          url.hash ||
          !url.pathname.endsWith(".3mf")
        )
          return invalid;
        return { ok: true, kind: "download", url: url.href } as const;
      };
      return await Promise.race([
        operation(),
        new Promise<MakerOnlinePageResult>((resolve) => {
          timer = setTimeout(() => {
            expired = true;
            resolve({ ok: false, code: "verification_required" });
          }, 120000);
        }),
      ]);
    } finally {
      if (timer !== undefined) clearTimeout(timer);
    }
  } catch {
    return { ok: false, code: "request_failed" };
  }
}

export function makerOnlineCaptureFromPage(
  pageUrl: string,
  result: MakerOnlinePageResult,
): BrowserCaptureMessage {
  const capture = buildBrowserCaptureMessage({
    provider: "MakerOnline",
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
export async function downloadMakerOnlineCandidate(options: {
  candidate: BrowserCaptureMessage["candidates"][number];
  result: MakerOnlinePageResult;
  ensureOriginPermission(origin: string): Promise<void>;
  signal?: AbortSignal;
}) {
  if (!options.result.ok || options.result.kind !== "download")
    throw new MarketplaceDownloadError(
      !options.result.ok && options.result.code === "verification_required"
        ? "MakerOnline requires verification on its page. Complete it there, then retry, or attach the downloaded file."
        : "MakerOnline could not authorize this download. Sign in on the source page and retry, or attach the downloaded file.",
    );
  return downloadMarketplaceFile({
    ...options,
    url: options.result.url,
    provider: "MakerOnline",
    allowedHosts: ["acop-prod-private.s3-accelerate.amazonaws.com"],
  });
}
