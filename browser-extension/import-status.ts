import { vaultFetch } from "./vault-fetch.ts";
import { CaptureAuthenticationError, CaptureResponseError } from "./core.ts";

export type ImportIntent = "import" | "review";

export const captureRecoveryMessages: Record<string, string> = {
  url_target_not_public:
    "PrintStash can't download from local or private addresses. Download the file and attach it instead.",
  staging_expired:
    "These files expired before they could be imported. Return to the source and capture them again.",
  provider_auth_failed:
    "Sign in to the source or reconnect the provider in PrintStash, then try again.",
  browser_device_name_in_use:
    "A browser with this name is already paired. Choose a different name.",
  resolve_failed:
    "PrintStash couldn't read this source. Open it to check access, or download the file and attach it.",
  capture_already_pending:
    "This capture is already in Pending Imports. Open the existing item to continue.",
  capture_upload_slots_incomplete:
    "Some files did not finish transferring. Reopen the source to continue the upload.",
  destination_not_editable: "Choose a collection you can edit.",
  files_need_review: "Check the files and choose what to import.",
  already_imported: "A capture from this source was already imported.",
};

export function captureRecoveryMessage(code: string | null | undefined, fallback: string) {
  return code && Object.hasOwn(captureRecoveryMessages, code)
    ? captureRecoveryMessages[code]
    : fallback;
}

interface CaptureProgress {
  state: "captured" | "resolving" | "review" | "importing" | "completed" | "failed" | "dismissed";
  review_reason: string | null;
  error_code: string | null;
  resulting_model_id: number | null;
  collection_name: string | null;
  completion: "complete" | "partial" | null;
}

export function captureAccount(vault: string, authorization: string, signal: AbortSignal) {
  return vaultFetch(`${vault}/api/v1/inbox/capture-capabilities`, {
    headers: { Authorization: `Bearer ${authorization}` },
    signal,
  }).then(async (response) => {
    if (!response.ok) throw new Error("Couldn't load capture settings.");
    const { owner_user_id: id } = await response.json();
    if (!Number.isSafeInteger(id) || id <= 0) throw new Error("Invalid capture account.");
    return id as number;
  });
}

export async function canResumeCapture(
  vault: string,
  authorization: string,
  itemId: number,
  signal: AbortSignal,
): Promise<boolean> {
  const response = await vaultFetch(`${vault}/api/v1/inbox/${itemId}/capture-upload`, {
    headers: { Authorization: `Bearer ${authorization}` },
    signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
  });
  return response.ok && (await response.json())?.recoverable === true;
}

export async function captureProgress(
  vault: string,
  authorization: string,
  itemId: number,
  signal: AbortSignal,
): Promise<CaptureProgress> {
  const response = await vaultFetch(`${vault}/api/v1/inbox/${itemId}/capture-status`, {
    headers: { Authorization: `Bearer ${authorization}` },
    signal: AbortSignal.any([signal, AbortSignal.timeout(10_000)]),
  });
  if (response.status === 401) throw new CaptureAuthenticationError();
  if (!response.ok)
    throw await CaptureResponseError.from(response, "Couldn't check import progress.");
  const value = await response.json();
  if (
    !["captured", "resolving", "importing", "review", "completed", "failed", "dismissed"].includes(
      value?.state,
    )
  ) {
    throw new Error("Invalid import progress.");
  }
  return value;
}
