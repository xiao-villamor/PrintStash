import { validateCaptureProfile } from "./print-profile.ts";
import type { CaptureSourceDraft, BrowserCaptureMessage } from "./capture-adapter.ts";
import { CaptureAuthenticationError } from "./core.ts";
import {
  CAPTURE_MAX_FILE_SIZE_BYTES,
  CAPTURE_MAX_TOTAL_SIZE_BYTES,
  CaptureCapacityError,
  captureRichFiles,
  type BrowserCaptureFile,
} from "./capture-transport.ts";

export type CaptureCandidate = BrowserCaptureMessage["candidates"][number];
export class CaptureRecoveryError extends Error {}
interface CaptureSession {
  item: { id: number; state: string };
  slots: Array<{
    source_file_id: string | null;
    state: "pending" | "uploaded";
    size_bytes: number;
  }>;
  recoverable: boolean;
}
function sessionReceipt(value: unknown): CaptureSession {
  if (
    typeof value !== "object" ||
    value === null ||
    !("item" in value) ||
    typeof value.item !== "object" ||
    value.item === null ||
    !("id" in value.item) ||
    typeof value.item.id !== "number" ||
    !Number.isSafeInteger(value.item.id) ||
    value.item.id <= 0 ||
    !("state" in value.item) ||
    typeof value.item.state !== "string" ||
    !("recoverable" in value) ||
    typeof value.recoverable !== "boolean" ||
    !("slots" in value) ||
    !Array.isArray(value.slots)
  )
    throw new CaptureRecoveryError(
      "PrintStash returned an invalid capture recovery receipt. No files were transferred.",
    );
  const slots: CaptureSession["slots"] = [];
  for (const slot of value.slots) {
    if (
      typeof slot !== "object" ||
      slot === null ||
      (typeof slot.source_file_id !== "string" && slot.source_file_id !== null) ||
      (slot.state !== "uploaded" && slot.state !== "pending") ||
      !Number.isSafeInteger(slot.size_bytes) ||
      slot.size_bytes <= 0
    )
      throw new CaptureRecoveryError("PrintStash returned an invalid capture slot receipt.");
    slots.push({
      source_file_id: slot.source_file_id,
      state: slot.state,
      size_bytes: slot.size_bytes,
    });
  }
  return {
    item: { id: value.item.id, state: value.item.state },
    slots,
    recoverable: value.recoverable,
  };
}
async function digest(bytes: ArrayBuffer): Promise<string> {
  return [...new Uint8Array(await crypto.subtle.digest("SHA-256", bytes))]
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

/** Only opaque request identity is persisted. URLs resolved by download never leave memory. */
export async function captureSequentialFiles(options: {
  vault: string;
  authorization: string;
  source: CaptureSourceDraft;
  title?: string;
  candidates: CaptureCandidate[];
  download(candidate: CaptureCandidate, signal?: AbortSignal): Promise<BrowserCaptureFile>;
  storage: {
    get(keys: string | string[]): Promise<Record<string, unknown>>;
    set(values: Record<string, unknown>): Promise<void>;
    remove(keys: string | string[]): Promise<void>;
  };
  progress(message: string): void;
  signal?: AbortSignal;
  fetchImpl?: typeof fetch;
}): Promise<{ id: number }> {
  const { vault, authorization, source, candidates, storage, signal, progress } = options;
  const fetchImpl = options.fetchImpl ?? fetch;
  const base = vault.replace(/\/$/, "");
  const headers = { Authorization: `Bearer ${authorization}`, "Content-Type": "application/json" };
  async function request(path: string, init?: RequestInit): Promise<Response> {
    const result = await fetchImpl(`${base}/api/v1/inbox${path}`, {
      ...init,
      headers: { ...headers, ...init?.headers },
      signal: signal
        ? AbortSignal.any([signal, AbortSignal.timeout(120_000)])
        : AbortSignal.timeout(120_000),
    });
    if (result.status === 401) throw new CaptureAuthenticationError();
    if (result.status === 507) throw new CaptureCapacityError("staging_capacity_exceeded");
    return result;
  }
  const capability = await request("/capture-capabilities");
  const support: unknown = capability.ok ? await capability.json() : null;
  const resumable =
    typeof support === "object" &&
    support !== null &&
    "resumable_slots" in support &&
    support.resumable_slots === true;
  if (capability.status === 404 || (capability.ok && !resumable)) {
    // Older servers retain their established transport and validation contract.
    const files: BrowserCaptureFile[] = [];
    for (const candidate of candidates) files.push(await options.download(candidate, signal));
    const result = await captureRichFiles({
      ...options,
      sourceUrl: source.canonical_url,
      captureSource: source,
      files,
    });
    if (
      typeof result !== "object" ||
      result === null ||
      !("id" in result) ||
      typeof result.id !== "number"
    )
      throw new Error("Invalid capture receipt.");
    return { id: result.id };
  }
  if (!capability.ok)
    throw new CaptureRecoveryError(`PrintStash readiness check failed (${capability.status}).`);
  const supportsProfiles =
    typeof support === "object" &&
    support !== null &&
    "schema_versions" in support &&
    Array.isArray(support.schema_versions) &&
    support.schema_versions.includes(3);
  const profiles = Object.fromEntries(
    candidates.filter((c) => c.profile).map((c) => [c.id, validateCaptureProfile(c.profile, c.id)]),
  );
  const key = `capture-request:${await digest(new TextEncoder().encode(JSON.stringify([base, authorization, source.canonical_url, candidates.map((c) => c.id).sort()])).buffer)}`;
  const saved = (await storage.get(key))[key];
  const requestKey =
    typeof saved === "string" && /^[a-zA-Z0-9_-]{1,64}$/.test(saved) ? saved : crypto.randomUUID();
  await storage.set({ [key]: requestKey });
  const created = await request("/capture-sessions", {
    method: "POST",
    body: JSON.stringify({
      request_key: requestKey,
      ...(supportsProfiles && Object.keys(profiles).length ? { schema_version: 3, profiles } : {}),
      source_url: source.canonical_url,
      capture_source: source,
      title: options.title ?? null,
      files: candidates.map((c) => ({
        id: c.id,
        name: c.filename,
        file_type: c.fileType,
        size: c.sizeBytes ?? null,
      })),
    }),
  });
  if (!created.ok)
    throw new CaptureRecoveryError(
      `PrintStash could not recover this capture (${created.status}). Reopen Pending Imports to inspect the previous capture.`,
    );
  const session = sessionReceipt(await created.json());
  if (["review", "importing", "completed"].includes(session.item.state)) {
    await storage.remove(key);
    return session.item;
  }
  if (!session.recoverable) {
    await storage.remove(key);
    throw new CaptureRecoveryError(
      "Staging expired. Capture again to download fresh files from this source.",
    );
  }
  let total = session.slots
    .filter((s) => s.state === "uploaded")
    .reduce((sum, s) => sum + s.size_bytes, 0);
  for (const [index, candidate] of candidates.entries()) {
    if (session.slots.some((s) => s.source_file_id === candidate.id && s.state === "uploaded"))
      continue;
    progress(`Downloading ${index + 1}/${candidates.length}: ${candidate.filename}`);
    const upload = await options.download(
      candidate,
      signal
        ? AbortSignal.any([signal, AbortSignal.timeout(120_000)])
        : AbortSignal.timeout(120_000),
    );
    total += upload.file.size;
    if (upload.file.size > CAPTURE_MAX_FILE_SIZE_BYTES || total > CAPTURE_MAX_TOTAL_SIZE_BYTES)
      throw new CaptureRecoveryError("Capture exceeds the supported size limit.");
    const checksum = await digest(await upload.file.arrayBuffer());
    const declared = await request(`/${session.item.id}/capture-files`, {
      method: "POST",
      body: JSON.stringify({
        id: candidate.id,
        filename: upload.filename,
        media_type: upload.mediaType,
        size_bytes: upload.file.size,
        sha256: checksum,
      }),
    });
    if (!declared.ok)
      throw new CaptureRecoveryError(
        `PrintStash could not reserve the next file (${declared.status}). Confirmed files remain available until staging expires.`,
      );
    const slot: unknown = await declared.json();
    if (
      typeof slot !== "object" ||
      slot === null ||
      !("id" in slot) ||
      typeof slot.id !== "string" ||
      !slot.id ||
      !("source_file_id" in slot) ||
      slot.source_file_id !== candidate.id ||
      !("filename" in slot) ||
      slot.filename !== upload.filename ||
      !("sha256" in slot) ||
      slot.sha256 !== checksum ||
      !("size_bytes" in slot) ||
      slot.size_bytes !== upload.file.size ||
      !("media_type" in slot) ||
      slot.media_type !== upload.mediaType
    )
      throw new CaptureRecoveryError(
        "PrintStash returned an invalid capture slot receipt. No bytes were sent to that slot.",
      );
    progress(`Transferring ${index + 1}/${candidates.length}: ${candidate.filename}`);
    const uploaded = await request(`/capture-upload-slots/${encodeURIComponent(slot.id)}`, {
      method: "PUT",
      headers: { "Content-Type": upload.mediaType },
      body: upload.file,
    });
    if (!uploaded.ok)
      throw new CaptureRecoveryError(
        `Transfer interrupted (${uploaded.status}). Reopen this source to recover confirmed files.`,
      );
  }
  progress("Transfer complete. Preparing review…");
  const finalized = await request(`/${session.item.id}/capture-upload-finalize`, {
    method: "POST",
  });
  if (!finalized.ok)
    throw new CaptureRecoveryError(
      `Could not confirm review (${finalized.status}). Reopen this source to recover the capture.`,
    );
  await storage.remove(key);
  return session.item;
}
