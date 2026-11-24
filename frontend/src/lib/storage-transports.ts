import { uiText } from "@/lib/locale";
import type { LibrarySourceKind } from "@/types";

/**
 * Remote storage transports, one entry each. `satisfies` makes this map
 * exhaustive over `LibrarySourceKind`, which `tests/repo/test_frontend_enums.py`
 * keeps equal to the backend enum: adding a transport there fails to type-check
 * here until it has a label.
 */
export type RemoteStorageKind = Exclude<LibrarySourceKind, "mounted">;

interface RemoteStorageKindPresentation {
  /** Label for the Library-source kind picker. */
  sourceLabel: () => string;
  /** Shown beside connections of this transport. */
  beta: boolean;
}

const REMOTE_STORAGE_KIND_PRESENTATION = {
  s3: { sourceLabel: () => uiText("S3 / compatible"), beta: false },
  webdav: { sourceLabel: () => uiText("WebDAV / Nextcloud"), beta: false },
  sftp: { sourceLabel: () => "SFTP", beta: false },
  gdrive: { sourceLabel: () => uiText("Google Drive"), beta: true },
} as const satisfies Record<RemoteStorageKind, RemoteStorageKindPresentation>;

export function isRemoteStorageKind(value: string): value is RemoteStorageKind {
  return Object.hasOwn(REMOTE_STORAGE_KIND_PRESENTATION, value);
}

export const REMOTE_STORAGE_KINDS: readonly RemoteStorageKind[] = Object.keys(
  REMOTE_STORAGE_KIND_PRESENTATION,
).filter(isRemoteStorageKind);

export function isLibrarySourceKind(value: string): value is LibrarySourceKind {
  return value === "mounted" || isRemoteStorageKind(value);
}

export function remoteStorageKindPresentation(
  kind: RemoteStorageKind,
): RemoteStorageKindPresentation {
  return REMOTE_STORAGE_KIND_PRESENTATION[kind];
}
