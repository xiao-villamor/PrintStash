/** Allowlisted profile facts. Missing facts stay absent; no print outcomes enter here. */
export interface CaptureProfile {
  id: string;
  name: string;
  origin: "provider" | "file";
  author?: string;
  creator_relationship?: "model_creator" | "other";
  printer?: string;
  estimated_seconds?: number;
  filament_grams?: number;
  plate_count?: number;
  materials?: string[];
}
export function validateCaptureProfile(value: unknown, expectedId: string): CaptureProfile {
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw new Error("Invalid profile.");
  const raw = value as Record<string, unknown>;
  const allowed = new Set([
    "id",
    "name",
    "origin",
    "author",
    "creator_relationship",
    "printer",
    "estimated_seconds",
    "filament_grams",
    "plate_count",
    "materials",
  ]);
  if (
    Object.keys(raw).some((key) => !allowed.has(key)) ||
    raw.id !== expectedId ||
    (raw.origin !== "provider" && raw.origin !== "file")
  )
    throw new Error("Invalid profile identity.");
  function text(key: string, limit = 512): string {
    const item = raw[key];
    if (typeof item !== "string" || !item.trim() || item.length > limit || /[\p{Cc}<>]/u.test(item))
      throw new Error("Invalid profile text.");
    return item;
  }
  const profile: CaptureProfile = { id: text("id", 256), name: text("name"), origin: raw.origin };
  for (const key of ["author", "printer"] as const) if (key in raw) profile[key] = text(key);
  if ("creator_relationship" in raw) {
    if (raw.creator_relationship !== "model_creator" && raw.creator_relationship !== "other")
      throw new Error("Invalid profile relationship.");
    profile.creator_relationship = raw.creator_relationship;
  }
  for (const [key, maximum] of [
    ["estimated_seconds", 31622400],
    ["filament_grams", 1000000],
    ["plate_count", 1000],
  ] as const) {
    if (!(key in raw)) continue;
    const number = raw[key];
    if (
      typeof number !== "number" ||
      !Number.isFinite(number) ||
      number <= 0 ||
      number > maximum ||
      (key === "plate_count" && !Number.isInteger(number))
    )
      throw new Error("Invalid profile estimate.");
    profile[key] = number;
  }
  if ("materials" in raw) {
    const materials = raw.materials;
    if (
      !Array.isArray(materials) ||
      materials.length > 64 ||
      materials.some(
        (item) =>
          typeof item !== "string" || !item.trim() || item.length > 128 || /[\p{Cc}<>]/u.test(item),
      )
    )
      throw new Error("Invalid profile materials.");
    profile.materials = materials as string[];
  }
  return profile;
}

/** Display rounding never changes the captured numeric evidence. */
export function profileSummary(profile: CaptureProfile): string {
  const details = [
    profile.author,
    profile.printer,
    profile.estimated_seconds
      ? profile.estimated_seconds < 60
        ? "<1 min"
        : `${Math.round(profile.estimated_seconds / 60)} min`
      : null,
    profile.filament_grams ? `${Number(profile.filament_grams.toFixed(2))} g` : null,
    profile.plate_count
      ? `${profile.plate_count} ${profile.plate_count === 1 ? "plate" : "plates"}`
      : null,
    profile.materials?.join(", "),
  ].filter(Boolean);
  return `${profile.origin === "provider" ? "Provider estimates" : "File estimates"} · ${details.length ? details.join(" · ") : "Details not captured"}`;
}
