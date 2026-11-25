import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import {
  Ban,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  CirclePause,
  CirclePlay,
  Clock,
  Folder,
  Play,
  RefreshCcw,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { lazy, Suspense, useId, useMemo, useState, type ReactNode } from "react";

import { StatePill } from "@/components/state-pill";
import { PrintJobReproducibility } from "@/components/print-job-reproducibility";
import { PrintJobCost } from "@/components/print-job-cost";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import {
  identifyPrintJob,
  printerArtifactInventory,
  printerFileTitle,
  remoteFileName,
} from "@/features/printers/artifact-identity";
import { printerKeys } from "@/features/printers/queries";
import { formatBytes, formatDuration, formatNumber, parseApiInstant } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { currentLocale, knownUiText, uiText } from "@/lib/locale";
import { Link } from "@/lib/link";
import { isArchivedPrintArtifact } from "@/lib/print-job-reproducibility";
import type { PrinterFileRead, PrinterRead, PrintJobRead, PrintJobState } from "@/types";
import "./printer-file-history.css";

const LibraryUpload = lazy(() =>
  import("./printer-library-upload").then((module) => ({ default: module.PrinterLibraryUpload })),
);

const QueueAgain = lazy(() =>
  import("./fleet-add-job").then((module) => ({ default: module.FleetQueueAgainAction })),
);

const outcomeLabels = {
  completed: "Completed",
  failed: "Failed",
  cancelled: "Cancelled",
  printing: "Printing",
  paused: "Paused",
  started: "Started",
  uploading: "Uploading",
  queued: "Queued",
} as const satisfies Record<PrintJobState, string>;

function isPrintJobState(value: string): value is PrintJobState {
  return Object.hasOwn(outcomeLabels, value);
}

const outcomeIcons = {
  completed: CircleCheck,
  failed: CircleAlert,
  cancelled: Ban,
  printing: CirclePlay,
  paused: CirclePause,
  started: CirclePlay,
  uploading: Clock,
  queued: Clock,
} as const satisfies Record<PrintJobState, LucideIcon>;

function JobOutcome({ job }: { job: PrintJobRead }) {
  return (
    <span className="inline-flex flex-col items-start gap-1">
      <StatePill icon={outcomeIcons[job.state]} destructive={job.state === "failed"}>
        {uiText(outcomeLabels[job.state])}
      </StatePill>
      {job.state === "completed" && job.started_at == null && (
        <span className="text-xs text-muted-foreground">{uiText("No print start recorded")}</span>
      )}
    </span>
  );
}

function useReadStatus(key: ReturnType<typeof printerKeys.files | typeof printerKeys.jobs>) {
  const client = useQueryClient();
  const fetching = useIsFetching({ queryKey: key, exact: true });
  const state = client.getQueryState(key);
  if (state?.status === "error") return "error";
  return fetching > 0 && !state?.dataUpdatedAt ? "loading" : "ready";
}

function dateValue(value: string | null, presentation: "short" | "full" = "full") {
  const date = parseApiInstant(value);
  if (!date) return uiText("Not recorded");
  const locale = currentLocale();
  const full = date.toLocaleString(locale, { dateStyle: "medium", timeStyle: "long" });
  return (
    <time dateTime={date.toISOString()} title={full}>
      {presentation === "short"
        ? date.toLocaleString(locale, { dateStyle: "short", timeStyle: "short" })
        : full}
    </time>
  );
}

function duration(job: PrintJobRead) {
  if (job.actual_duration_s != null) return job.actual_duration_s;
  const start = parseApiInstant(job.started_at);
  const end = parseApiInstant(job.finished_at);
  if (!start || !end) return null;
  const seconds = (end.getTime() - start.getTime()) / 1000;
  return seconds >= 0 ? seconds : null;
}

function directory(path: string) {
  const index = path.lastIndexOf("/");
  return index < 0 ? "" : path.slice(0, index);
}

function ancestors(path: string) {
  const result = [path];
  while (path) {
    path = directory(path);
    result.unshift(path);
  }
  return result;
}

function buildFolders(files: readonly PrinterFileRead[]) {
  const folders = new Map<string, string[]>([["", []]]);
  for (const file of files) {
    for (const path of ancestors(directory(file.remote_filename)).slice(1)) {
      if (folders.has(path)) continue;
      folders.set(path, []);
      folders.get(directory(path))?.push(path);
    }
  }
  return folders;
}

function matches(query: string, fields: readonly (string | null | undefined)[]) {
  return fields.some((field) => field?.toLocaleLowerCase(currentLocale()).includes(query));
}

function matchLabel(method: string) {
  switch (method) {
    case "upload_history":
      return uiText("Upload history");
    case "vault_marker":
      return uiText("Saved Revision ID");
    case "filename":
      return uiText("Original filename");
    case "size":
      return uiText("File size");
    default:
      return uiText("Other match method");
  }
}

function WorkHeader({ id, title, count }: { id: string; title: string; count?: string }) {
  return (
    <header className="printer-explorer-header">
      <h2 id={id} className="text-sm font-semibold">
        {title}
      </h2>
      {count && <span className="text-xs text-muted-foreground">{count}</span>}
    </header>
  );
}

function Details({ fields }: { fields: readonly (readonly [string, ReactNode])[] }) {
  return (
    <dl className="printer-explorer-details">
      {fields.map(([label, value]) => (
        <div key={label}>
          <dt className="text-muted-foreground">{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function TableState({ message }: { message: string }) {
  return (
    <tr>
      <td colSpan={4} className="text-muted-foreground">
        <p role="status">{message}</p>
      </td>
    </tr>
  );
}

function FolderBranch({
  path,
  rootLabel,
  folders,
  current,
  expanded,
  navigate,
}: {
  path: string;
  rootLabel: string;
  folders: ReadonlyMap<string, string[]>;
  current: string;
  expanded: ReadonlySet<string>;
  navigate: (path: string) => void;
}) {
  const open = expanded.has(path);
  const children = folders.get(path) ?? [];
  return (
    <li>
      <button
        type="button"
        className="printer-explorer-folder hover:bg-muted focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        aria-current={path === current ? "location" : undefined}
        onClick={() => navigate(path)}
      >
        {children.length > 0 &&
          (open ? <ChevronDown aria-hidden="true" /> : <ChevronRight aria-hidden="true" />)}
        <Folder aria-hidden="true" />
        <span>{path ? remoteFileName(path) : rootLabel}</span>
      </button>
      {open && children.length > 0 && (
        <ul>
          {children.map((child) => (
            <FolderBranch
              key={child}
              path={child}
              rootLabel={rootLabel}
              folders={folders}
              current={current}
              expanded={expanded}
              navigate={navigate}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

interface PrinterFilesProps {
  printer: PrinterRead;
  files: readonly PrinterFileRead[];
  activeFilename?: string | null;
  authBlockReason?: string | null;
  startingId: number | null;
  deletingId: number | null;
  syncing: boolean;
  notes?: ReactNode;
  onSync: () => void;
  onStart: (file: PrinterFileRead) => void;
  onDelete: (file: PrinterFileRead) => void;
  onOpenJobs: () => void;
}

export function PrinterFiles({
  printer,
  files,
  activeFilename,
  authBlockReason,
  startingId,
  deletingId,
  syncing,
  notes,
  onSync,
  onStart,
  onDelete,
  onOpenJobs,
}: PrinterFilesProps) {
  useUiLocale();
  const id = useId();
  const status = useReadStatus(printerKeys.files(printer.id));
  const rootLabel =
    printer.provider === "moonraker"
      ? "gcodes"
      : printer.provider === "bambu_lan"
        ? "/cache"
        : uiText("Printer files");
  const inventory = useMemo(() => files.filter((file) => !file.missing_since), [files]);
  const folders = useMemo(() => buildFolders(inventory), [inventory]);
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [uploadedFilename, setUploadedFilename] = useState<string | null>(null);
  const uploadedFile = inventory.find((file) => file.remote_filename === uploadedFilename);
  const uploadAction = printer.access.can_print && printer.capabilities.can_upload && (
    <Button size="sm" disabled={authBlockReason != null} onClick={() => setUploadOpen(true)}>
      {uiText("Upload from library")}
    </Button>
  );
  const current = ancestors(path).findLast((candidate) => folders.has(candidate)) ?? "";
  const breadcrumbs = ancestors(current);
  const query = search.trim().toLocaleLowerCase(currentLocale());
  const visible = inventory.filter((file) =>
    query
      ? matches(query, [file.remote_filename, file.original_filename, file.model_name])
      : directory(file.remote_filename) === current,
  );
  const selected = visible.find((file) => file.id === selectedId) ?? visible[0];

  function navigate(folder: string) {
    setPath(folder);
    setSearch("");
    setSelectedId(null);
  }

  const selectionBusy = startingId !== null || deletingId !== null;
  const activeSelection = selected !== undefined && selected.remote_filename === activeFilename;
  const librarySource =
    selected?.model_id &&
    (selected.matched_by === "upload_history" || selected.matched_by === "vault_marker");
  return (
    <section className="printer-explorer printer-explorer-inventory" aria-labelledby={id}>
      <WorkHeader
        id={id}
        title={uiText("Printer files")}
        count={
          printer.capabilities.can_list_files && status === "ready"
            ? uiText("printerFiles.syncedCount", { count: inventory.length })
            : undefined
        }
      />
      {uploadOpen && (
        <Suspense
          fallback={
            <p role="status" className="p-3 text-sm">
              {uiText("Loading...")}
            </p>
          }
        >
          <LibraryUpload
            printer={printer}
            onClose={() => setUploadOpen(false)}
            onUploaded={(filename) => {
              setUploadedFilename(filename);
              setUploadOpen(false);
            }}
          />
        </Suspense>
      )}
      {uploadedFilename !== null && (
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-2 text-sm">
          <p role="status" className="min-w-0 break-words">
            {uiText("Uploaded {value1}", { value1: uploadedFilename })}
          </p>
          {printer.capabilities.can_list_files && uploadedFile && (
            <Button
              variant="outline"
              size="xs"
              onClick={() => {
                navigate(directory(uploadedFile.remote_filename));
                setSelectedId(uploadedFile.id);
              }}
            >
              {uiText("Inspect file")}
            </Button>
          )}
        </div>
      )}
      {notes}
      {!printer.capabilities.can_list_files ? (
        <div className="printer-explorer-unsupported">
          <p className="text-sm text-muted-foreground">
            {uiText("Printer file inventory is not supported by this provider.")}
          </p>
          <p className="text-sm">{uiText("Files you send appear in Jobs.")}</p>
          <Button size="sm" variant="outline" onClick={onOpenJobs}>
            {uiText("Open in Jobs")}
          </Button>
          {uploadAction}
        </div>
      ) : (
        <>
          <div className="printer-explorer-toolbar">
            <nav aria-label={uiText("Printer file path")} className="printer-explorer-path">
              {breadcrumbs.map((crumb, index) => (
                <span key={crumb}>
                  {index > 0 && <ChevronRight aria-hidden="true" />}
                  {crumb === current ? (
                    <span className="font-semibold" aria-current="location">
                      {crumb ? remoteFileName(crumb) : rootLabel}
                    </span>
                  ) : (
                    <Button variant="ghost" size="xs" onClick={() => navigate(crumb)}>
                      {crumb ? remoteFileName(crumb) : rootLabel}
                    </Button>
                  )}
                </span>
              ))}
            </nav>
            <Input
              className="printer-explorer-search"
              type="search"
              aria-label={uiText("Search files")}
              placeholder={uiText("Search files")}
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
            <Button
              size="sm"
              variant="outline"
              onClick={onSync}
              loading={syncing}
              disabled={!printer.access.can_admin}
              title={authBlockReason ?? uiText("Sync printer files")}
            >
              <RefreshCcw aria-hidden="true" className="h-3.5 w-3.5" />
              {uiText("Sync printer files")}
            </Button>
            {uploadAction}
          </div>
          <div className="printer-explorer-files">
            <nav aria-label={uiText("Folders")} className="printer-explorer-folders">
              <h3 className="mb-2 text-sm font-semibold">{uiText("Folders")}</h3>
              <ul>
                <FolderBranch
                  path=""
                  rootLabel={rootLabel}
                  folders={folders}
                  current={current}
                  expanded={new Set(breadcrumbs)}
                  navigate={navigate}
                />
              </ul>
            </nav>
            <div className="printer-explorer-table">
              <table aria-label={uiText("Printer files")}>
                <thead>
                  <tr>
                    <th scope="col">{uiText("Name")}</th>
                    <th scope="col">{uiText("Size")}</th>
                    <th scope="col">{uiText("Library")}</th>
                    <th scope="col">{uiText("Last seen")}</th>
                  </tr>
                </thead>
                <tbody>
                  {visible.map((file) => {
                    const title = printerFileTitle(file);
                    const detail = query
                      ? file.remote_filename
                      : remoteFileName(file.remote_filename);
                    return (
                      <tr
                        key={file.id}
                        data-selected={file.id === selected?.id}
                        onClick={({ currentTarget }) => {
                          setSelectedId(file.id);
                          currentTarget.querySelector("button")?.focus();
                        }}
                      >
                        <td>
                          <h3>
                            <button
                              type="button"
                              className="printer-explorer-pick focus-visible:ring-2 focus-visible:ring-ring"
                              aria-pressed={file.id === selected?.id}
                            >
                              {title}
                            </button>
                          </h3>
                          {detail !== title && (
                            <p className="printer-explorer-secondary">{detail}</p>
                          )}
                        </td>
                        <td>{file.size_bytes === null ? "—" : formatBytes(file.size_bytes)}</td>
                        <td>
                          <StatePill icon={file.model_id ? CircleCheck : undefined}>
                            {uiText(file.model_id ? "Matched" : "External")}
                          </StatePill>
                        </td>
                        <td>{dateValue(file.last_seen_at, "short")}</td>
                      </tr>
                    );
                  })}
                  {visible.length === 0 && (
                    <TableState
                      message={
                        status === "error"
                          ? uiText("Could not load printer files.")
                          : status === "loading"
                            ? uiText("Loading...")
                            : query
                              ? uiText("No files match your search.")
                              : inventory.length === 0
                                ? uiText("No printer files synced yet.")
                                : uiText("No files in this folder.")
                      }
                    />
                  )}
                </tbody>
              </table>
            </div>
            <aside className="printer-explorer-inspector" aria-label={uiText("Selected file")}>
              <h3 className="printer-explorer-secondary">{uiText("Selected file")}</h3>
              {selected ? (
                <>
                  <p className="printer-explorer-title">{printerFileTitle(selected)}</p>
                  <p className="break-all font-mono text-xs">{selected.remote_filename}</p>
                  <Details
                    fields={[
                      [
                        uiText("Original filename"),
                        selected.original_filename ?? uiText("Not recorded"),
                      ],
                      [uiText("Model"), selected.model_name ?? "—"],
                      ...(selected.plate_index != null
                        ? [[uiText("Plate"), String(selected.plate_index)] as const]
                        : []),
                      [
                        uiText("Size"),
                        selected.size_bytes === null ? "—" : formatBytes(selected.size_bytes),
                      ],
                      [uiText("Source"), uiText(librarySource ? "Library" : "Printer")],
                      ...(selected.model_id
                        ? [[uiText("Library match"), matchLabel(selected.matched_by)] as const]
                        : []),
                      [uiText("Last seen"), dateValue(selected.last_seen_at)],
                    ]}
                  />
                  <div className="printer-explorer-actions">
                    {(printer.provider !== "bambu_lan" ||
                      (selected.plate_index != null &&
                        selected.file_id != null &&
                        selected.original_filename?.toLowerCase().endsWith(".gcode.3mf") &&
                        selected.remote_filename.toLowerCase().endsWith(".3mf") &&
                        ["upload_history", "vault_marker"].includes(selected.matched_by)) ||
                      /\.(?:gcode|gco|g)$/i.test(selected.remote_filename)) && (
                      <Button
                        size="sm"
                        onClick={() => onStart(selected)}
                        loading={startingId === selected.id}
                        disabled={
                          !printer.access.can_print ||
                          !printer.capabilities.can_start ||
                          selectionBusy
                        }
                        title={authBlockReason ?? uiText("Start this printer file")}
                      >
                        <Play aria-hidden="true" className="h-3.5 w-3.5" />
                        {uiText("Start")}
                      </Button>
                    )}
                    {selected.model_id && (
                      <Button asChild size="sm" variant="outline">
                        <Link href={`/models/${selected.model_id}`}>{uiText("Open Model")}</Link>
                      </Button>
                    )}
                    {printer.provider !== "bambu_lan" && (
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => onDelete(selected)}
                        loading={deletingId === selected.id}
                        disabled={
                          !printer.access.can_admin ||
                          printer.provider !== "moonraker" ||
                          selectionBusy ||
                          activeSelection
                        }
                        title={
                          activeSelection
                            ? uiText("Cannot delete active print file")
                            : (authBlockReason ?? uiText("Delete this printer file"))
                        }
                      >
                        <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
                        {uiText("Delete")}
                      </Button>
                    )}
                  </div>
                </>
              ) : (
                <p className="mt-2 text-sm text-muted-foreground">
                  {uiText("Select a file to inspect it.")}
                </p>
              )}
            </aside>
          </div>
          <p className="printer-explorer-footnote text-xs text-muted-foreground">
            {printer.provider === "bambu_lan"
              ? uiText(
                  "To print a sliced 3MF from the Library, choose its plate when sending the Revision.",
                )
              : uiText(
                  "An active print file cannot be deleted. Start checks the loaded material; it does not check that the G-code is safe for this printer.",
                )}
          </p>
        </>
      )}
    </section>
  );
}

export function PrinterRecentRuns({
  printerId,
  jobs,
  inventory,
  jobsHref,
}: {
  printerId: number;
  jobs: readonly PrintJobRead[];
  inventory: ReturnType<typeof printerArtifactInventory>;
  jobsHref: string;
}) {
  useUiLocale();
  const status = useReadStatus(printerKeys.jobs(printerId));
  const recent = jobs
    .flatMap((job) => {
      if (job.printer_id !== printerId) return [];
      if (job.state !== "completed" && job.state !== "failed" && job.state !== "cancelled")
        return [];
      const finished = parseApiInstant(job.finished_at);
      return finished ? [{ job, finished }] : [];
    })
    .sort((left, right) => right.finished.getTime() - left.finished.getTime())
    .slice(0, 4);
  if (status === "ready" && recent.length === 0 && jobs.length > 0) return null;

  return (
    <section className="printer-desk-section" aria-label={uiText("Print history")}>
      <div className="printer-desk-heading">
        <h2>{uiText("Print history")}</h2>
        <Link href={jobsHref} className={buttonVariants({ variant: "outline", size: "xs" })}>
          {uiText("Jobs")}
        </Link>
      </div>
      {recent.length > 0 && (
        <ul className="divide-y divide-border">
          {recent.map(({ job }) => {
            const elapsed = duration(job);
            return (
              <li key={job.id} className="printer-recent-run">
                <div className="min-w-0">
                  <p className="break-words font-medium">
                    {identifyPrintJob(job, inventory).title}
                  </p>
                  <div className="mt-0.5 text-xs text-muted-foreground">
                    {dateValue(job.finished_at, "short")}
                    {elapsed !== null && ` · ${formatDuration(elapsed)}`}
                  </div>
                  {job.production_cost && <PrintJobCost cost={job.production_cost} />}
                </div>
                <div
                  className={
                    job.state === "completed"
                      ? "text-success"
                      : job.state === "failed"
                        ? "text-destructive"
                        : "text-muted-foreground"
                  }
                >
                  <JobOutcome job={job} />
                </div>
              </li>
            );
          })}
        </ul>
      )}
      {(status === "error" || recent.length === 0) && (
        <p className="py-1 text-xs text-muted-foreground">
          {status === "error"
            ? uiText("Could not load print history.")
            : status === "loading"
              ? uiText("Loading...")
              : uiText("No print jobs yet.")}
        </p>
      )}
    </section>
  );
}

export function PrinterJobHistory({
  printer,
  jobs,
  files,
}: {
  printer: PrinterRead;
  jobs: readonly PrintJobRead[];
  files: readonly PrinterFileRead[];
}) {
  useUiLocale();
  const id = useId();
  const status = useReadStatus(printerKeys.jobs(printer.id));
  const [search, setSearch] = useState("");
  const [outcome, setOutcome] = useState<PrintJobState | "">("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [evidenceId, setEvidenceId] = useState<number | null>(null);
  const inventory = useMemo(() => printerArtifactInventory(files), [files]);
  const rows = jobs.map((job) => ({
    job,
    ...identifyPrintJob(job, inventory),
    elapsed: duration(job),
  }));
  const query = search.trim().toLocaleLowerCase(currentLocale());
  const visible = rows.filter(
    ({ job, title, artifact, original }) =>
      (!outcome || job.state === outcome) &&
      (!query || matches(query, [title, artifact, original, job.remote_filename, `#${job.id}`])),
  );
  const selected = visible.find(({ job }) => job.id === selectedId) ?? visible[0];
  const evidence = rows.find(({ job }) => job.id === evidenceId);
  const archived = (job: PrintJobRead) =>
    job.model_id > 0 && isArchivedPrintArtifact(job.artifact_evidence);

  return (
    <section className="printer-explorer" aria-labelledby={id}>
      <WorkHeader
        id={id}
        title={uiText("Print history")}
        count={
          status === "ready" ? uiText("printerJobs.runCount", { count: jobs.length }) : undefined
        }
      />
      <div className="printer-explorer-toolbar">
        <Input
          className="printer-explorer-search"
          type="search"
          aria-label={uiText("Search runs")}
          placeholder={uiText("Search runs")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
        <select
          className={`${inputClasses} printer-explorer-outcome`}
          aria-label={uiText("Outcome")}
          value={outcome}
          onChange={({ target: { value } }) => setOutcome(isPrintJobState(value) ? value : "")}
        >
          <option value="">{uiText("All outcomes")}</option>
          {Object.entries(outcomeLabels).map(([state, label]) => (
            <option key={state} value={state}>
              {uiText(label)}
            </option>
          ))}
        </select>
      </div>
      <div className="printer-explorer-jobs">
        <div className="printer-explorer-table">
          <table aria-label={uiText("Print history")}>
            <thead>
              <tr>
                <th scope="col">{uiText("Print")}</th>
                <th scope="col">{uiText("Outcome")}</th>
                <th scope="col">{uiText("Finished")}</th>
                <th scope="col">{uiText("Duration")}</th>
              </tr>
            </thead>
            <tbody>
              {visible.map(({ job, title, artifact, original, elapsed }) => (
                <tr
                  key={job.id}
                  data-selected={job.id === selected?.job.id}
                  onClick={({ currentTarget }) => {
                    setSelectedId(job.id);
                    currentTarget.querySelector("button")?.focus();
                  }}
                >
                  <td>
                    <h3>
                      <button
                        type="button"
                        className="printer-explorer-pick focus-visible:ring-2 focus-visible:ring-ring"
                        aria-pressed={job.id === selected?.job.id}
                        aria-describedby={`${id}-run-${job.id} ${id}-outcome-${job.id}`}
                      >
                        {title}
                      </button>
                    </h3>
                    <p id={`${id}-run-${job.id}`} className="printer-explorer-secondary">
                      {uiText("Run #{value1}", { value1: String(job.id) })}
                      {(original ?? artifact) !== title ? ` · ${original ?? artifact}` : ""}
                    </p>
                    {job.production_cost && <PrintJobCost cost={job.production_cost} />}
                  </td>
                  <td
                    id={`${id}-outcome-${job.id}`}
                    className={
                      job.state === "completed"
                        ? "text-success"
                        : job.state === "failed"
                          ? "text-destructive"
                          : undefined
                    }
                  >
                    <JobOutcome job={job} />
                  </td>
                  <td>{dateValue(job.finished_at, "short")}</td>
                  <td>{elapsed === null ? "—" : formatDuration(elapsed)}</td>
                </tr>
              ))}
              {visible.length === 0 && (
                <TableState
                  message={
                    status === "error"
                      ? uiText("Could not load print history.")
                      : status === "loading"
                        ? uiText("Loading...")
                        : query || outcome
                          ? uiText("No runs match your filters.")
                          : uiText("No print jobs yet.")
                  }
                />
              )}
            </tbody>
          </table>
        </div>
        <aside className="printer-explorer-inspector" aria-label={uiText("Selected run")}>
          <h3 className="printer-explorer-secondary">{uiText("Selected run")}</h3>
          {selected ? (
            <>
              <p className="printer-explorer-title">{selected.title}</p>
              <p className="printer-explorer-secondary">
                {uiText("Run #{value1}", { value1: String(selected.job.id) })}
                {selected.original && selected.original !== selected.title
                  ? ` · ${selected.original}`
                  : ""}
              </p>
              <Details
                fields={[
                  [uiText("Result"), <JobOutcome key="result" job={selected.job} />],
                  [uiText("Created"), dateValue(selected.job.created_at)],
                  [uiText("Started"), dateValue(selected.job.started_at)],
                  [uiText("Finished"), dateValue(selected.job.finished_at)],
                  [
                    uiText("Duration"),
                    selected.elapsed === null
                      ? uiText("Not recorded")
                      : formatDuration(selected.elapsed),
                  ],
                  [
                    uiText("Progress"),
                    `${formatNumber(selected.job.progress * 100, { maximumFractionDigits: 0 })}%`,
                  ],
                  [
                    uiText("Source"),
                    uiText(selected.job.source === "vault" ? "Library" : "Printer"),
                  ],
                  ...(selected.job.source === "external"
                    ? [[uiText("Evidence"), knownUiText(selected.job.artifact_evidence)] as const]
                    : []),
                ]}
              />
              <div className="printer-explorer-actions">
                <Suspense fallback={null}>
                  <QueueAgain key={selected.job.id} job={selected.job} printer={printer} />
                </Suspense>
                {archived(selected.job) && (
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/models/${selected.job.model_id}`}>{uiText("Open Model")}</Link>
                  </Button>
                )}
                <Button size="sm" variant="outline" onClick={() => setEvidenceId(selected.job.id)}>
                  {uiText("Evidence")}
                </Button>
              </div>
            </>
          ) : (
            <p className="mt-2 text-sm text-muted-foreground">
              {uiText("Select a run to inspect it.")}
            </p>
          )}
        </aside>
      </div>
      <Modal
        open={evidence !== undefined}
        onClose={() => setEvidenceId(null)}
        title={evidence?.title ?? uiText("Evidence")}
        className="max-w-3xl"
      >
        {evidence && (
          <PrintJobReproducibility
            job={evidence.job}
            previewHref={archived(evidence.job) ? `/models/${evidence.job.model_id}` : null}
          />
        )}
      </Modal>
    </section>
  );
}
