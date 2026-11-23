/** Mirrors backend `app/schemas/previews.py`. */
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
  | "disabled"
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
  "disabled",
  "pending",
  "queued",
  "running",
  "ready",
  "failed",
  "cancelled",
  "unsupported",
];

export type PreviewImageKind = "photo" | "render" | "slicer_render";

export interface PreviewImage {
  image_kind?: "render";
  id: PreviewViewId;
  role: PreviewViewRole;
  url: string;
  thumbnail_url?: string;
}
export interface SourceImage {
  id: `source-${number}`;
  role: "source";
  source_id: number;
  position: number;
  provider: string;
  url: string;
}
export type GalleryImage = PreviewImage | SourceImage;
export interface ArchivePreviewImage {
  image_kind: "photo" | "slicer_render";
  file_id: number;
  url: string;
  plate: number | null;
}
export type PreviewImageId = PreviewViewId | `archive-${number}`;
export type CarouselImage =
  | PreviewImage
  | (ArchivePreviewImage & { id: `archive-${number}`; role: "archive" });

interface ModelPreviewMedia {
  cover_image_kind?: PreviewImageKind;
  large_cover_image_kind?: PreviewImageKind;
  large_cover_url?: string;
  large_cover_thumbnail_url?: string;
  large_cover_view?: PreviewViewId;
  archive_images?: ArchivePreviewImage[];
}

export interface PublishedPreviews {
  profile: "orbit" | "planar";
  manifest_url: string;
  /** View whose bytes match the current cover, when a match is known. */
  cover_view?: PreviewViewId;
  views: PreviewImage[];
}
/** The Model has no live mesh Artifact; only its cover can be shown. */
export interface NoPreviewSubject extends ModelPreviewMedia {
  kind: "none";
  cover_camera: "unknown";
  /** Optional when reading a Vault predating provider galleries. */
  source_images?: SourceImage[];
}
/** The view set of the one mesh Artifact selected for this Model. */
export interface ArtifactPreviews extends ModelPreviewMedia {
  kind: "artifact";
  cover_camera: "unknown";
  /** Optional when reading a Vault predating provider galleries. */
  source_images?: SourceImage[];
  file_id: number;
  state: PreviewState;
  reason: string | null;
  /** The last valid generation; kept while a replacement is produced. */
  published: PublishedPreviews | null;
}
export type ModelPreviews = NoPreviewSubject | ArtifactPreviews;

export const NO_PREVIEWS: NoPreviewSubject = { kind: "none", cover_camera: "unknown" };

export function coverImageKind(
  previews: ModelPreviews,
  size: "thumbnail" | "full",
): PreviewImageKind {
  return (
    (size === "full" ? previews.large_cover_image_kind : undefined) ??
    previews.cover_image_kind ??
    "render"
  );
}

/** Published images, or none when no valid generation exists yet. */
export function publishedViews(previews: ModelPreviews): CarouselImage[] {
  const generated = previews.kind === "artifact" ? (previews.published?.views ?? []) : [];
  return [
    ...generated,
    ...(previews.archive_images ?? []).map((image, index) => ({
      ...image,
      id: `archive-${index}` as const,
      role: "archive" as const,
    })),
  ];
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
function isPreviewImageKind(value: PreviewImageKind): boolean {
  return value === "photo" || value === "render" || value === "slicer_render";
}

export function isModelPreviews(wire: ModelPreviews): boolean {
  // oxlint-disable anti-slop/no-runtime-typeof -- Foreign HTTP JSON must establish its closed case, Artifact identity and view addresses before it can replace a displayed preview.
  if (wire?.cover_camera !== "unknown") return false;
  if (
    wire.source_images !== undefined &&
    (!Array.isArray(wire.source_images) ||
      wire.source_images.some(
        (image) =>
          !/^source-\d+$/.test(image?.id) ||
          image.role !== "source" ||
          !Number.isSafeInteger(image.source_id) ||
          image.source_id <= 0 ||
          !Number.isSafeInteger(image.position) ||
          image.position < 0 ||
          typeof image.provider !== "string" ||
          typeof image.url !== "string",
      ))
  )
    return false;
  if (
    (wire.cover_image_kind !== undefined && !isPreviewImageKind(wire.cover_image_kind)) ||
    (wire.large_cover_image_kind !== undefined && !isPreviewImageKind(wire.large_cover_image_kind))
  )
    return false;
  if (wire.large_cover_url !== undefined && typeof wire.large_cover_url !== "string") return false;
  if (
    wire.large_cover_thumbnail_url !== undefined &&
    typeof wire.large_cover_thumbnail_url !== "string"
  )
    return false;
  if (wire.large_cover_view !== undefined && !PREVIEW_VIEW_IDS.includes(wire.large_cover_view))
    return false;
  if (
    wire.archive_images !== undefined &&
    (!Array.isArray(wire.archive_images) ||
      !wire.archive_images.every(
        (image) =>
          Number.isSafeInteger(image?.file_id) &&
          image.file_id > 0 &&
          typeof image.url === "string" &&
          (image.image_kind === "photo" || image.image_kind === "slicer_render") &&
          (image.plate === null || (Number.isSafeInteger(image.plate) && image.plate > 0)),
      ))
  )
    return false;
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
        (published.cover_view === undefined || PREVIEW_VIEW_IDS.includes(published.cover_view)) &&
        Array.isArray(published.views) &&
        published.views.length >= 1 &&
        published.views.length <= 7 &&
        published.views.every(
          (view) =>
            PREVIEW_VIEW_IDS.includes(view?.id) &&
            PREVIEW_VIEW_ROLES.includes(view.role) &&
            (view.image_kind === undefined || view.image_kind === "render") &&
            typeof view.url === "string" &&
            (view.thumbnail_url === undefined || typeof view.thumbnail_url === "string"),
        )))
  );
  // oxlint-enable anti-slop/no-runtime-typeof
}
