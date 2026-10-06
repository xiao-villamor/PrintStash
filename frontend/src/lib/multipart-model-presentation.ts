import type { useI18n } from "@/lib/i18n";
import { parseApiError } from "@/lib/errors";

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
