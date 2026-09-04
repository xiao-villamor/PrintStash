export type ProvenanceOrigin = "confirmed" | "inferred" | "user";
export type InboxCompletion = "complete" | "partial" | null;

/**
 * Preserve the semantic origin for presentation layers to translate.
 *
 * The domain package deliberately does not choose a display language. A UI
 * can map this key to its own typed message catalog.
 */
export function provenanceOriginKey(origin: ProvenanceOrigin): ProvenanceOrigin {
  return origin;
}

export function formatInboxCompletion(
  completion: InboxCompletion,
): "Complete" | "Partial" | "In progress" {
  if (completion === "complete") return "Complete";
  if (completion === "partial") return "Partial";
  return "In progress";
}
