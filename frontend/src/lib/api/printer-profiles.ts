import {
  getJson,
  GetJsonOptions,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import {
  PrinterProfileCreate,
  PrinterProfileRead,
  PrinterProfileUpdate,
} from "@/types";

export function listPrinterProfiles(
  options?: GetJsonOptions,
): Promise<PrinterProfileRead[]> {
  return getJson<PrinterProfileRead[]>("/api/v1/printer-profiles", options);
}

export function createPrinterProfile(
  payload: PrinterProfileCreate,
): Promise<PrinterProfileRead> {
  return sendJson<PrinterProfileRead>(
    "/api/v1/printer-profiles",
    "POST",
    payload,
  );
}

export function updatePrinterProfile(
  id: number,
  payload: PrinterProfileUpdate,
): Promise<PrinterProfileRead> {
  return sendJson<PrinterProfileRead>(
    `/api/v1/printer-profiles/${id}`,
    "PATCH",
    payload,
  );
}

export function deletePrinterProfile(id: number): Promise<void> {
  return sendAction(`/api/v1/printer-profiles/${id}`, "DELETE");
}
