import {
  printJobArtifactLabel,
  resolvePrintJobReproducibility,
} from "@/lib/print-job-reproducibility";
import type { PrinterFileRead, PrintJobRead } from "@/types";

export function remoteFileName(path: string) {
  return path.slice(path.lastIndexOf("/") + 1);
}

export function printerFileTitle(file: PrinterFileRead) {
  return file.model_name ?? file.original_filename ?? remoteFileName(file.remote_filename);
}

/** Index the permission-filtered inventory already available on this printer. */
export function printerArtifactInventory(files: readonly PrinterFileRead[]) {
  const byPath = new Map<string, PrinterFileRead>();
  const byFileId = new Map<number, PrinterFileRead>();
  for (const file of files) {
    if (file.missing_since) continue;
    byPath.set(file.remote_filename, file);
    if (file.file_id !== null && file.model_id !== null) byFileId.set(file.file_id, file);
  }
  return { byPath, byFileId };
}

type PrinterArtifactInventory = ReturnType<typeof printerArtifactInventory>;

export function identifyPrintJob(job: PrintJobRead, inventory: PrinterArtifactInventory) {
  const file = inventory.byFileId.get(job.file_id);
  const visibleMatch = file?.model_id === job.model_id ? file : undefined;
  const identity = resolvePrintJobReproducibility(job).identity;
  const artifact =
    job.source === "external" && !identity.gcode_file && inventory.byPath.has(job.remote_filename)
      ? remoteFileName(job.remote_filename)
      : printJobArtifactLabel(job);
  return {
    title:
      visibleMatch?.model_name ??
      visibleMatch?.original_filename ??
      identity.display_name ??
      artifact,
    artifact,
    original: visibleMatch?.original_filename,
  };
}

interface RunningPrintIdentity {
  title: string;
  filename: string;
}

export function identifyRunningPrint(
  printerId: number,
  reportedFilename: string | undefined,
  jobs: readonly PrintJobRead[],
  inventory: PrinterArtifactInventory,
): RunningPrintIdentity | null {
  if (reportedFilename?.trim()) {
    const reported = inventory.byPath.get(reportedFilename);
    if (reported?.model_id) {
      return { title: printerFileTitle(reported), filename: reportedFilename };
    }
    return { title: reportedFilename, filename: reportedFilename };
  }

  // The printer is active, but a sparse transition can omit the unchanged
  // filename on an exact repeat. Never borrow a queued, uploading or past run.
  const managed = jobs.find(
    (job) =>
      job.printer_id === printerId &&
      job.source === "vault" &&
      job.finished_at === null &&
      (job.state === "started" || job.state === "printing" || job.state === "paused"),
  );
  return managed
    ? { title: identifyPrintJob(managed, inventory).title, filename: managed.remote_filename }
    : null;
}
