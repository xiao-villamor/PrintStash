import { editHeaders, requireEditingReceipt } from "./editing";
import type { EditingBase } from "@/types/editing";
import { withSessionRequest } from "@/lib/session-transport";
import {
  getJson,
  GetJsonOptions,
  getWsUrl,
  requestApi,
  jsonHeaders,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import {
  Dashboard,
  MoonrakerConfigRead,
  PrinterDiagnostics,
  PrintJobRead,
  PrinterFileRead,
  PrinterCreate,
  PrinterRead,
  PrinterPermissionRead,
  PrinterRole,
  PrinterStatusResponse,
  PrinterUpdate,
  PrinterMaterialStateRead,
  ManualMaterialStateUpdate,
  SendToPrinter,
  StartPrinterFile,
} from "@/types";

export function listPrinters(group?: string, options?: GetJsonOptions): Promise<PrinterRead[]> {
  const query = group ? `?group=${encodeURIComponent(group)}` : "";
  return getJson<PrinterRead[]>(`/api/v1/printers${query}`, options);
}

export function getDashboard(options?: GetJsonOptions): Promise<Dashboard> {
  return getJson<Dashboard>("/api/v1/printers/dashboard", options);
}

export function getPrinter(id: number, options?: GetJsonOptions): Promise<PrinterRead> {
  return getJson<PrinterRead>(`/api/v1/printers/${id}`, options);
}

export function getPrinterDiagnostics(
  id: number,
  options?: GetJsonOptions,
): Promise<PrinterDiagnostics> {
  // Live connectivity check — caching it would defeat the "re-run checks" button.
  return getJson<PrinterDiagnostics>(`/api/v1/printers/${id}/diagnostics`, options);
}

export function getMoonrakerConfig(
  id: number,
  options?: GetJsonOptions,
): Promise<MoonrakerConfigRead> {
  return getJson<MoonrakerConfigRead>(`/api/v1/printers/${id}/config`, options);
}

export function createPrinter(payload: PrinterCreate): Promise<PrinterRead> {
  return sendJson<PrinterRead>("/api/v1/printers", "POST", payload);
}

export async function updatePrinter(
  id: number,
  payload: PrinterUpdate,
  options: { base: EditingBase; signal?: AbortSignal },
): Promise<PrinterRead> {
  const saved = await requestApi<PrinterRead>(`/api/v1/printers/${id}`, {
    method: "PATCH",
    headers: { ...jsonHeaders(), ...editHeaders("printer", id, options.base) },
    body: JSON.stringify(payload),
    signal: options.signal,
  });
  requireEditingReceipt(saved, options.base);
  if (saved.id !== id) throw new Error("printer_identity_mismatch");
  return saved;
}

export function getPrinterMaterialState(id: number): Promise<PrinterMaterialStateRead> {
  return getJson<PrinterMaterialStateRead>(`/api/v1/printers/${id}/material-state`, {
    fresh: true,
  });
}

export function updatePrinterManualMaterialState(
  id: number,
  payload: ManualMaterialStateUpdate,
): Promise<PrinterMaterialStateRead> {
  return sendJson<PrinterMaterialStateRead>(
    `/api/v1/printers/${id}/material-state/manual`,
    "PUT",
    payload,
  );
}

export function deletePrinter(id: number): Promise<void> {
  return sendAction(`/api/v1/printers/${id}`, "DELETE");
}

export function listPrinterPermissions(
  id: number,
  options: GetJsonOptions = {},
): Promise<PrinterPermissionRead[]> {
  return getJson<PrinterPermissionRead[]>(`/api/v1/printers/${id}/permissions`, {
    ...options,
    fresh: true,
  });
}

export function updatePrinterPermission(
  printerId: number,
  userId: number,
  role: PrinterRole,
): Promise<PrinterPermissionRead> {
  return requestApi<PrinterPermissionRead>(`/api/v1/printers/${printerId}/permissions/${userId}`, {
    method: "PUT",
    headers: jsonHeaders(),
    body: JSON.stringify({ role }),
  });
}

export function deletePrinterPermission(printerId: number, userId: number): Promise<void> {
  return requestApi<void>(`/api/v1/printers/${printerId}/permissions/${userId}`, {
    method: "DELETE",
  });
}

export function sendToPrinter(id: number, payload: SendToPrinter): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(`/api/v1/printers/${id}/send`, "POST", payload);
}

export function startPrinterFile(
  id: number,
  payload: StartPrinterFile,
  options?: GetJsonOptions,
): Promise<PrintJobRead> {
  return requestApi<PrintJobRead>(`/api/v1/printers/${id}/start`, {
    method: "POST",
    headers: jsonHeaders(),
    body: JSON.stringify(payload),
    signal: options?.signal,
  });
}

function printerControl(id: number, action: "pause" | "resume" | "cancel"): Promise<void> {
  return sendAction(`/api/v1/printers/${id}/${action}`, "POST");
}

export function pausePrinter(id: number): Promise<void> {
  return printerControl(id, "pause");
}

export function resumePrinter(id: number): Promise<void> {
  return printerControl(id, "resume");
}

export function cancelPrinter(id: number): Promise<void> {
  return printerControl(id, "cancel");
}

export function setPrinterTemperature(
  id: number,
  heater: "extruder" | "bed",
  target: number,
): Promise<void> {
  return sendJson(`/api/v1/printers/${id}/temperature`, "POST", {
    heater,
    target,
  });
}

export function homePrinter(id: number, axes?: string): Promise<void> {
  return sendJson(`/api/v1/printers/${id}/home`, "POST", { axes: axes ?? null });
}

export function emergencyStopPrinter(id: number): Promise<void> {
  return sendAction(`/api/v1/printers/${id}/emergency_stop`, "POST");
}

export function getPrinterStatus(id: number): Promise<PrinterStatusResponse> {
  // One-shot live snapshot — always fetch fresh.
  return getJson<PrinterStatusResponse>(`/api/v1/printers/${id}/status`, {
    fresh: true,
  });
}

export function listPrinterFiles(id: number, options?: GetJsonOptions): Promise<PrinterFileRead[]> {
  return getJson<PrinterFileRead[]>(`/api/v1/printers/${id}/files`, options);
}

export function syncPrinterFiles(id: number, options?: GetJsonOptions): Promise<PrinterFileRead[]> {
  return requestApi<PrinterFileRead[]>(`/api/v1/printers/${id}/files/sync`, {
    method: "POST",
    headers: jsonHeaders(),
    body: "{}",
    signal: options?.signal,
  });
}

export async function deletePrinterFile(
  id: number,
  printerFileId: number,
  options?: GetJsonOptions,
): Promise<PrinterFileRead[]> {
  return requestApi<PrinterFileRead[]>(`/api/v1/printers/${id}/files/${printerFileId}`, {
    method: "DELETE",
    signal: options?.signal,
  });
}

export function listPrinterJobs(
  id: number,
  limit = 50,
  options?: GetJsonOptions,
): Promise<PrintJobRead[]> {
  return getJson<PrintJobRead[]>(`/api/v1/printers/${id}/jobs?limit=${limit}`, options);
}

export async function openPrinterWS(id: number, signal?: AbortSignal): Promise<WebSocket> {
  let closeOpened = () => {};
  try {
    return await withSessionRequest(async (request) => {
      const { ticket } = await requestApi<{ ticket: string; expires_in: number }>(
        `/api/v1/printers/${id}/ws-ticket`,
        { method: "POST", headers: jsonHeaders(), body: "{}", signal: request.signal },
      );
      request.assertCurrent();
      const ws = new WebSocket(
        getWsUrl(`/api/v1/printers/${id}/ws?ticket=${encodeURIComponent(ticket)}`),
      );
      closeOpened = () => ws.close();
      return ws;
    }, signal);
  } catch (error) {
    closeOpened();
    throw error;
  }
}
