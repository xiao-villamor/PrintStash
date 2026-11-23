import { getJson, sendJson } from "@/lib/api/request";

export interface PrinterConfigFileEntry {
  path: string;
  size_bytes: number;
  modified: number;
  writable: boolean;
}

export interface PrinterConfigFile {
  path: string;
  content: string;
  revision: string;
  writable: boolean;
}

function fileUrl(printerId: number, path: string) {
  return `/api/v1/printers/${printerId}/config/file?${new URLSearchParams({ path })}`;
}

export function listConfigFiles(printerId: number, signal?: AbortSignal) {
  return getJson<{ files: PrinterConfigFileEntry[] }>(
    `/api/v1/printers/${printerId}/config/files`,
    {
      signal,
    },
  );
}

export function readConfigFile(printerId: number, path: string, signal?: AbortSignal) {
  return getJson<PrinterConfigFile>(fileUrl(printerId, path), { signal });
}

export function saveConfigFile(printerId: number, file: PrinterConfigFile, content: string) {
  return sendJson<PrinterConfigFile>(fileUrl(printerId, file.path), "PUT", {
    content,
    base_revision: file.revision,
  });
}
