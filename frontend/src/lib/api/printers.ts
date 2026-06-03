import {
  getJson,
  getWsUrl,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import {
  Dashboard,
  PrintJobRead,
  PrinterFileRead,
  PrinterCreate,
  PrinterRead,
  PrinterStatusResponse,
  PrinterUpdate,
  SendToPrinter,
  StartPrinterFile,
} from "@/types";

export function listPrinters(group?: string): Promise<PrinterRead[]> {
  const query = group ? `?group=${encodeURIComponent(group)}` : "";
  return getJson<PrinterRead[]>(`/api/v1/printers${query}`);
}

export function getDashboard(): Promise<Dashboard> {
  return getJson<Dashboard>("/api/v1/printers/dashboard");
}

export function getPrinter(id: number): Promise<PrinterRead> {
  return getJson<PrinterRead>(`/api/v1/printers/${id}`);
}

export function createPrinter(
  payload: PrinterCreate,
  apiKey?: string,
): Promise<PrinterRead> {
  return sendJson<PrinterRead>("/api/v1/printers", "POST", payload, apiKey);
}

export function updatePrinter(
  id: number,
  payload: PrinterUpdate,
  apiKey?: string,
): Promise<PrinterRead> {
  return sendJson<PrinterRead>(
    `/api/v1/printers/${id}`,
    "PATCH",
    payload,
    apiKey,
  );
}

export function deletePrinter(id: number, apiKey?: string): Promise<void> {
  return sendAction(`/api/v1/printers/${id}`, "DELETE", apiKey);
}

export function sendToPrinter(
  id: number,
  payload: SendToPrinter,
  apiKey?: string,
): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(
    `/api/v1/printers/${id}/send`,
    "POST",
    payload,
    apiKey,
  );
}

export function startPrinterFile(
  id: number,
  payload: StartPrinterFile,
  apiKey?: string,
): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(
    `/api/v1/printers/${id}/start`,
    "POST",
    payload,
    apiKey,
  );
}

function printerControl(
  id: number,
  action: "pause" | "resume" | "cancel",
  apiKey?: string,
): Promise<void> {
  return sendAction(`/api/v1/printers/${id}/${action}`, "POST", apiKey);
}

export function pausePrinter(id: number, apiKey?: string): Promise<void> {
  return printerControl(id, "pause", apiKey);
}

export function resumePrinter(id: number, apiKey?: string): Promise<void> {
  return printerControl(id, "resume", apiKey);
}

export function cancelPrinter(id: number, apiKey?: string): Promise<void> {
  return printerControl(id, "cancel", apiKey);
}

export function getPrinterStatus(id: number): Promise<PrinterStatusResponse> {
  return getJson<PrinterStatusResponse>(`/api/v1/printers/${id}/status`);
}

export function listPrinterFiles(id: number): Promise<PrinterFileRead[]> {
  return getJson<PrinterFileRead[]>(`/api/v1/printers/${id}/files`);
}

export function syncPrinterFiles(
  id: number,
  apiKey?: string,
): Promise<PrinterFileRead[]> {
  return sendJson<PrinterFileRead[]>(
    `/api/v1/printers/${id}/files/sync`,
    "POST",
    {},
    apiKey,
  );
}

export function listPrinterJobs(
  id: number,
  limit = 50,
): Promise<PrintJobRead[]> {
  return getJson<PrintJobRead[]>(`/api/v1/printers/${id}/jobs?limit=${limit}`);
}

export function openPrinterWS(id: number): WebSocket {
  return new WebSocket(getWsUrl(`/api/v1/printers/${id}/ws`));
}
