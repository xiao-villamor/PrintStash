import {
  getJson,
  getWsUrl,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import {
  Dashboard,
  PrinterDiagnostics,
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

export function getPrinterDiagnostics(id: number): Promise<PrinterDiagnostics> {
  return getJson<PrinterDiagnostics>(`/api/v1/printers/${id}/diagnostics`);
}

export function createPrinter(payload: PrinterCreate): Promise<PrinterRead> {
  return sendJson<PrinterRead>("/api/v1/printers", "POST", payload);
}

export function updatePrinter(
  id: number,
  payload: PrinterUpdate,
): Promise<PrinterRead> {
  return sendJson<PrinterRead>(
    `/api/v1/printers/${id}`,
    "PATCH",
    payload,
  );
}

export function deletePrinter(id: number): Promise<void> {
  return sendAction(`/api/v1/printers/${id}`, "DELETE");
}

export function sendToPrinter(
  id: number,
  payload: SendToPrinter,
): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(
    `/api/v1/printers/${id}/send`,
    "POST",
    payload,
  );
}

export function startPrinterFile(
  id: number,
  payload: StartPrinterFile,
): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(
    `/api/v1/printers/${id}/start`,
    "POST",
    payload,
  );
}

function printerControl(
  id: number,
  action: "pause" | "resume" | "cancel",
): Promise<void> {
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

export function getPrinterStatus(id: number): Promise<PrinterStatusResponse> {
  return getJson<PrinterStatusResponse>(`/api/v1/printers/${id}/status`);
}

export function listPrinterFiles(id: number): Promise<PrinterFileRead[]> {
  return getJson<PrinterFileRead[]>(`/api/v1/printers/${id}/files`);
}

export function syncPrinterFiles(id: number): Promise<PrinterFileRead[]> {
  return sendJson<PrinterFileRead[]>(
    `/api/v1/printers/${id}/files/sync`,
    "POST",
    {},
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
