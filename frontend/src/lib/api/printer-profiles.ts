import { editHeaders, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";
import {
  getJson,
  GetJsonOptions,
  sendAction,
  sendJson,
  requestApi,
  jsonHeaders,
} from "@/lib/api/request";
import { PrinterProfileCreate, PrinterProfileRead, PrinterProfileUpdate } from "@/types";

export function listPrinterProfiles(options?: GetJsonOptions): Promise<PrinterProfileRead[]> {
  return getJson<PrinterProfileRead[]>("/api/v1/printer-profiles", options);
}

export function createPrinterProfile(payload: PrinterProfileCreate): Promise<PrinterProfileRead> {
  return sendJson<PrinterProfileRead>("/api/v1/printer-profiles", "POST", payload);
}

export async function updatePrinterProfile(
  id: number,
  payload: PrinterProfileUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<PrinterProfileRead> {
  const saved = await requestApi<PrinterProfileRead>(`/api/v1/printer-profiles/${id}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("printer-profile", id, options.base) },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== id) throw new Error("profile_identity_mismatch");
  return saved;
}

export function deletePrinterProfile(id: number): Promise<void> {
  return sendAction(`/api/v1/printer-profiles/${id}`, "DELETE");
}
