import { getJson, sendAction, sendJson } from "@/lib/api/request";
import type {
  FleetSummary,
  MaintenanceLog,
  MaintenanceWindow,
  PrinterRoutingUpdate,
  PrintJobRead,
  QueueJobCreate,
  QueueJobUpdate,
  BatchCreate,
  CompatibilityRead,
  PrintBatchRead,
} from "@/types";

export function getFleetSummary(): Promise<FleetSummary> {
  return getJson<FleetSummary>("/api/v1/fleet/summary", { fresh: true });
}

export function listFleetQueue(historyLimit = 20, historyOffset = 0): Promise<PrintJobRead[]> {
  const params = new URLSearchParams({
    history_limit: String(historyLimit),
    history_offset: String(historyOffset),
  });
  return getJson<PrintJobRead[]>(`/api/v1/fleet/queue?${params}`, { fresh: true });
}

export function enqueueFleetJob(payload: QueueJobCreate): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>("/api/v1/fleet/queue", "POST", payload);
}

export function checkFleetCompatibility(
  fileId: number,
  printerIds: number[],
): Promise<CompatibilityRead> {
  return sendJson<CompatibilityRead>("/api/v1/fleet/compatibility", "POST", {
    file_id: fileId,
    printer_ids: printerIds,
  });
}

export function createFleetBatch(payload: BatchCreate): Promise<PrintBatchRead> {
  return sendJson<PrintBatchRead>("/api/v1/fleet/batches", "POST", payload);
}

export function decideFleetOperatorGate(
  id: number,
  action: "release" | "hold",
): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(`/api/v1/fleet/queue/${id}/operator-decision`, "POST", { action });
}

export function updateFleetJob(id: number, payload: QueueJobUpdate): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(`/api/v1/fleet/queue/${id}`, "PATCH", payload);
}

export function deleteFleetJob(id: number): Promise<void> {
  return sendAction(`/api/v1/fleet/queue/${id}`, "DELETE");
}

export function retryFleetJob(id: number): Promise<PrintJobRead> {
  return sendJson<PrintJobRead>(`/api/v1/fleet/queue/${id}/retry`, "POST", {});
}

export function updatePrinterRouting(id: number, payload: PrinterRoutingUpdate) {
  return sendJson(`/api/v1/fleet/printers/${id}/routing`, "PATCH", payload);
}

export function listMaintenanceWindows(id: number): Promise<MaintenanceWindow[]> {
  return getJson<MaintenanceWindow[]>(`/api/v1/fleet/printers/${id}/maintenance-windows`, {
    fresh: true,
  });
}

export function createMaintenanceWindow(
  id: number,
  payload: { starts_at: string; ends_at: string; reason?: string | null },
): Promise<MaintenanceWindow> {
  return sendJson<MaintenanceWindow>(
    `/api/v1/fleet/printers/${id}/maintenance-windows`,
    "POST",
    payload,
  );
}

export function deleteMaintenanceWindow(id: number, windowId: number): Promise<void> {
  return sendAction(`/api/v1/fleet/printers/${id}/maintenance-windows/${windowId}`, "DELETE");
}

export function listMaintenanceLog(id: number): Promise<MaintenanceLog[]> {
  return getJson<MaintenanceLog[]>(`/api/v1/fleet/printers/${id}/maintenance-log`, { fresh: true });
}

export function createMaintenanceLog(
  id: number,
  payload: { category: string; note: string; performed_at?: string },
): Promise<MaintenanceLog> {
  return sendJson<MaintenanceLog>(`/api/v1/fleet/printers/${id}/maintenance-log`, "POST", payload);
}

export function deleteMaintenanceLog(id: number, logId: number): Promise<void> {
  return sendAction(`/api/v1/fleet/printers/${id}/maintenance-log/${logId}`, "DELETE");
}
