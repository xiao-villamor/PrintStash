import type { useI18n } from "@/lib/i18n";
import { parseApiError } from "@/lib/errors";
import type { MultipartPartRead } from "@/types";

/**
 * Keep server details behind the same error translation seam as the rest of
 * the app. Multipart endpoints also have a few domain-specific detail codes;
 * those should get useful, localised copy instead of exposing the code itself.
 */
export function multipartError(
  cause: unknown,
  t: ReturnType<typeof useI18n>["t"],
  fallback: Parameters<ReturnType<typeof useI18n>["t"]>[0],
): string {
  const parsed = parseApiError(cause);
  // Multipart operations need copy from the active catalog. The global
  // userMessage seam intentionally stays locale-neutral, so map the boundary
  // categories here instead of exposing a server detail code or English copy
  // in a Spanish vault.
  if (parsed.code === "network_unreachable") return t("multipart.networkError");
  if (parsed.status === 403 || parsed.code.endsWith("_permission_denied")) {
    return t("multipart.permissionError");
  }
  if (parsed.status === 404 || parsed.code.endsWith("_not_found")) {
    return t("multipart.notFoundError");
  }
  if (parsed.code === "unknown" || parsed.code === "offline") return t(fallback);
  if (parsed.code.startsWith("multipart_") || parsed.code.startsWith("part_")) {
    return t(fallback);
  }
  if (parsed.code.endsWith("_failed")) return t(fallback);
  // Unknown details must remain operation-specific and localized. Do not fall
  // through to userMessage(), whose generic catalog is deliberately English.
  return t(fallback);
}

export function detailHref(id: number, returnTo?: string): string {
  if (!returnTo) return `/multipart-models/${id}`;
  const search = new URLSearchParams({ return: returnTo });
  return `/multipart-models/${id}?${search.toString()}`;
}

/** What a part still needs before it can be printed, from the Models it links. */
export type PartReadiness = "ready" | "choose-variant" | "no-gcode" | "unavailable";

function isPrintable(model: MultipartPartRead["models"][number]): boolean {
  return model.available && model.gcode_revision_count > 0;
}

export function partReadiness(part: MultipartPartRead): PartReadiness {
  if (part.models.every((model) => !model.available)) return "unavailable";
  if (!part.models.some(isPrintable)) return "no-gcode";
  return part.models.length > 1 ? "choose-variant" : "ready";
}

/** Parts with at least one linked Model that already has a G-code revision. */
export function printablePartCount(parts: MultipartPartRead[]): number {
  return parts.filter((part) => part.models.some(isPrintable)).length;
}

/** Most Models a multipart set can start with. */
export const MULTIPART_INITIAL_LIMIT = 100;
