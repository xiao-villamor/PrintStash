import {
  getJson,
  GetJsonOptions,
  getWsUrl,
  authHeaders,
  getUrl,
  handleResponse,
  invalidateApiCache,
  sendAction,
  sendJson,
} from "@/lib/api/request";
import { getStoredToken } from "@/lib/auth";
import {
  Dashboard,
  MoonrakerConfigRead,
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

export function listPrinters(
  group?: string,
  options?: GetJsonOptions,
): Promise<PrinterRead[]> {
  const query = group ? `?group=${encodeURIComponent(group)}` : "";
  return getJson<PrinterRead[]>(`/api/v1/printers${query}`, options);
}

export function getDashboard(): Promise<Dashboard> {
  return getJson<Dashboard>("/api/v1/printers/dashboard");
}

export function getPrinter(id: number): Promise<PrinterRead> {
  return getJson<PrinterRead>(`/api/v1/printers/${id}`);
}

export function getPrinterDiagnostics(id: number): Promise<PrinterDiagnostics> {
  // Live connectivity check — caching it would defeat the "re-run checks" button.
  return getJson<PrinterDiagnostics>(`/api/v1/printers/${id}/diagnostics`, {
    fresh: true,
  });
}

export function getMoonrakerConfig(id: number): Promise<MoonrakerConfigRead> {
  return getJson<MoonrakerConfigRead>(`/api/v1/printers/${id}/config`, {
    fresh: true,
  });
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
  // One-shot live snapshot — always fetch fresh.
  return getJson<PrinterStatusResponse>(`/api/v1/printers/${id}/status`, {
    fresh: true,
  });
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

export async function deletePrinterFile(
  id: number,
  printerFileId: number,
): Promise<PrinterFileRead[]> {
  const res = await fetch(
    getUrl(`/api/v1/printers/${id}/files/${printerFileId}`),
    {
      method: "DELETE",
      headers: authHeaders(),
    },
  );
  invalidateApiCache(`/api/v1/printers/${id}/files/${printerFileId}`);
  return handleResponse<PrinterFileRead[]>(res);
}

export function listPrinterJobs(
  id: number,
  limit = 50,
): Promise<PrintJobRead[]> {
  return getJson<PrintJobRead[]>(`/api/v1/printers/${id}/jobs?limit=${limit}`);
}

export function openPrinterWS(id: number): WebSocket {
  const token = getStoredToken();
  const path = token
    ? `/api/v1/printers/${id}/ws?token=${encodeURIComponent(token)}`
    : `/api/v1/printers/${id}/ws`;
  return new WebSocket(getWsUrl(path));
}
