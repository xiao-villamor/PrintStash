/** Mirrors backend `app/schemas/previews.py`; covers never carry a known camera. */
export type PreviewViewId =
  | "orbit-0"
  | "orbit-1"
  | "orbit-2"
  | "orbit-3"
  | "top"
  | "bottom"
  | "face"
  | "reverse"
  | "edge";
export type PreviewViewRole = "orbit" | "top" | "bottom" | "face" | "reverse" | "edge";
export type PreviewState =
  | "pending"
  | "queued"
  | "running"
  | "ready"
  | "failed"
  | "cancelled"
  | "unsupported";

export const PREVIEW_VIEW_IDS: readonly PreviewViewId[] = [
  "orbit-0",
  "orbit-1",
  "orbit-2",
  "orbit-3",
  "top",
  "bottom",
  "face",
  "reverse",
  "edge",
];
export const PREVIEW_VIEW_ROLES: readonly PreviewViewRole[] = [
  "orbit",
  "top",
  "bottom",
  "face",
  "reverse",
  "edge",
];
export const PREVIEW_STATES: readonly PreviewState[] = [
  "pending",
  "queued",
  "running",
  "ready",
  "failed",
  "cancelled",
  "unsupported",
];

export interface PreviewImage {
  id: PreviewViewId;
  role: PreviewViewRole;
  url: string;
}
export interface PublishedPreviews {
  profile: "orbit" | "planar";
  manifest_url: string;
  views: PreviewImage[];
}
/** The Model has no live mesh Artifact; only its cover can be shown. */
export interface NoPreviewSubject {
  kind: "none";
  cover_camera: "unknown";
}
/** The view set of the one mesh Artifact selected for this Model. */
export interface ArtifactPreviews {
  kind: "artifact";
  cover_camera: "unknown";
  file_id: number;
  state: PreviewState;
  reason: string | null;
  /** The last valid generation; kept while a replacement is produced. */
  published: PublishedPreviews | null;
}
export type ModelPreviews = NoPreviewSubject | ArtifactPreviews;

export const NO_PREVIEWS: NoPreviewSubject = { kind: "none", cover_camera: "unknown" };

/** Published images, or none when no valid generation exists yet. */
export function publishedViews(previews: ModelPreviews): PreviewImage[] {
  return previews.kind === "artifact" && previews.published ? previews.published.views : [];
}

/** The selected mesh Artifact, so views of different Artifacts never mix. */
export function previewArtifact(previews: ModelPreviews): number | null {
  return previews.kind === "artifact" ? previews.file_id : null;
}

/** Whether generation may still change what this Model can show. */
export function previewsInFlight(previews: ModelPreviews): boolean {
  return (
    previews.kind === "artifact" &&
    (previews.state === "pending" || previews.state === "queued" || previews.state === "running")
  );
}

/**
 * Boundary check for wire data that overlays a cached browse snapshot. The
 * value arrives typed by its declared contract; this confirms it actually holds.
 */
export function isModelPreviews(wire: ModelPreviews): boolean {
  // oxlint-disable anti-slop/no-runtime-typeof -- Foreign HTTP JSON must establish its closed case, Artifact identity and view addresses before it can replace a displayed preview.
  if (wire?.cover_camera !== "unknown") return false;
  if (wire.kind === "none") return true;
  if (wire.kind !== "artifact") return false;
  const published = wire.published;
  return (
    Number.isSafeInteger(wire.file_id) &&
    wire.file_id > 0 &&
    PREVIEW_STATES.includes(wire.state) &&
    (wire.reason === null || typeof wire.reason === "string") &&
    (published === null ||
      ((published?.profile === "orbit" || published?.profile === "planar") &&
        typeof published.manifest_url === "string" &&
        Array.isArray(published.views) &&
        published.views.length >= 1 &&
        published.views.length <= 6 &&
        published.views.every(
          (view) =>
            PREVIEW_VIEW_IDS.includes(view?.id) &&
            PREVIEW_VIEW_ROLES.includes(view.role) &&
            typeof view.url === "string",
        )))
  );
  // oxlint-enable anti-slop/no-runtime-typeof
}
