import { CaptureAuthenticationError } from "./core.ts";

export interface CaptureDestination {
  id: number;
  label: string;
}

export function captureTags(value: string): string[] {
  const tags = [
    ...new Set(
      value
        .split(",")
        .map((tag) => tag.trim())
        .filter(Boolean),
    ),
  ];
  if (tags.length > 100 || tags.some((tag) => tag.length > 128))
    throw new Error("Use at most 100 tags, each no longer than 128 characters.");
  return tags;
}

/** Only editable collection labels, never a general library credential. */
export async function captureDestinations(options: {
  vault: string;
  authorization: string;
  query: string;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}): Promise<CaptureDestination[]> {
  const response = await (options.fetchImpl ?? fetch)(
    `${options.vault.replace(/\/$/, "")}/api/v1/inbox/capture-destinations?q=${encodeURIComponent(options.query)}`,
    {
      headers: { Authorization: `Bearer ${options.authorization}` },
      signal: options.signal
        ? AbortSignal.any([options.signal, AbortSignal.timeout(30_000)])
        : AbortSignal.timeout(30_000),
    },
  );
  if (response.status === 401) throw new CaptureAuthenticationError();
  if (!response.ok)
    throw new Error(
      "Collection suggestions are unavailable. Choose the collection in Pending Imports, or update PrintStash.",
    );
  const value: unknown = await response.json();
  if (!Array.isArray(value) || value.length > 20)
    throw new Error("Invalid collection suggestions.");
  return value.map((entry: unknown) => {
    if (
      typeof entry !== "object" ||
      entry === null ||
      !("id" in entry) ||
      typeof entry.id !== "number" ||
      !Number.isSafeInteger(entry.id) ||
      entry.id <= 0 ||
      !("label" in entry) ||
      typeof entry.label !== "string" ||
      entry.label.length > 4096
    )
      throw new Error("Invalid collection suggestions.");
    return { id: entry.id, label: entry.label };
  });
}

/** A cover is an explicit user-selected file; no provider URL or image is fetched implicitly. */
export function selectedCaptureCover(file: File | undefined) {
  if (!file) return undefined;
  const type =
    file.type ||
    (
      { jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp" } as Record<
        string,
        string
      >
    )[file.name.split(".").pop()?.toLowerCase() ?? ""];
  if (
    !type ||
    !["image/jpeg", "image/png", "image/webp"].includes(type) ||
    file.size === 0 ||
    file.size > 15 * 1024 * 1024
  )
    throw new Error("Choose a JPEG, PNG or WebP cover up to 15 MiB.");
  return { id: "cover", filename: file.name, mediaType: type, file };
}
