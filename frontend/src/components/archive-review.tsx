"use client";

import { useEffect, useMemo, useState } from "react";
import { CheckSquare, ChevronRight, File, Folder, Search, Square } from "lucide-react";

import { getJobStatus } from "@/lib/api/jobs";
import { selectArchiveEntries } from "@/lib/api/models";
import { useAuth } from "@/lib/auth-context";
import { collectionDisplayPath } from "@/lib/collection-display";
import { userMessage } from "@/lib/errors";
import { formatBytes } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiMessage, uiText } from "@/lib/locale";
import { useCollections } from "@/lib/queries";
import { listTasks, trackImportJob, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import type { ArchiveEntry, ArchiveManifest, JobStatus } from "@/types";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";

class ArchiveReviewUnavailableError extends Error {}

function manifestFromJob(job: JobStatus): ArchiveManifest {
  const result = job.result;
  if (
    job.kind !== "ingestion.archive_inspect" ||
    job.state !== "completed" ||
    result?.kind !== "archive_manifest" ||
    !result.archive_id ||
    !result.archive_name ||
    !Array.isArray(result.entries)
  ) {
    throw new ArchiveReviewUnavailableError(uiText("This ZIP is not ready for review."));
  }
  return {
    archive_id: result.archive_id,
    archive_name: result.archive_name,
    entries: result.entries,
  };
}

function segments(entry: ArchiveEntry): string[] {
  return entry.name.replaceAll("\\", "/").split("/");
}

function parentOf(entry: ArchiveEntry): string | null {
  const parts = segments(entry);
  return parts.length > 1 ? parts.slice(0, -1).join("/") : null;
}

export function ArchiveReviewDialog({ jobId, onClose }: { jobId: string; onClose: () => void }) {
  useUiLocale();
  const { user } = useAuth();
  const { data: collections = [] } = useCollections();
  const [manifest, setManifest] = useState<ArchiveManifest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [folder, setFolder] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [pickedCollection, setPickedCollection] = useState<string | null | undefined>(undefined);
  const [submitting, setSubmitting] = useState(false);
  const task = listTasks().find((item) => item.jobId === jobId);
  const writableCollections = collections.filter(
    (item) => item.effective_role === "edit" || item.effective_role === "admin",
  );
  const collection =
    pickedCollection !== undefined
      ? pickedCollection
      : (task?.archiveCollection ??
        (!user?.is_superuser ? (writableCollections[0]?.path ?? null) : null));
  const tags = task?.archiveTags ?? [];

  useEffect(() => {
    let active = true;
    getJobStatus(jobId)
      .then(manifestFromJob)
      .then((value) => {
        if (active) setManifest(value);
      })
      .catch((error: Error) => {
        if (active)
          setLoadError(
            error instanceof ArchiveReviewUnavailableError ? error.message : userMessage(error),
          );
      });
    return () => {
      active = false;
    };
  }, [jobId]);

  const importable = useMemo(
    () => manifest?.entries.filter((entry) => entry.file_type !== null) ?? [],
    [manifest],
  );
  const search = query.trim().toLocaleLowerCase();
  const visibleFiles = (
    search
      ? importable.filter((entry) => entry.name.toLocaleLowerCase().includes(search))
      : importable.filter((entry) => parentOf(entry) === folder)
  ).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  const folders = useMemo(() => {
    const children = new Set<string>();
    for (const entry of importable) {
      const parent = parentOf(entry);
      if (parent === null) continue;
      const prefix = folder === null ? "" : `${folder}/`;
      if (!parent.startsWith(prefix)) continue;
      const remainder = parent.slice(prefix.length);
      const child = remainder.split("/")[0];
      if (child) children.add(prefix + child);
    }
    return [...children].sort((a, b) => a.localeCompare(b));
  }, [folder, importable]);
  const breadcrumbs =
    folder?.split("/").map((_, index, parts) => parts.slice(0, index + 1).join("/")) ?? [];

  function toggle(name: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  async function importSelected() {
    if (!manifest || selected.size === 0 || submitting || (!user?.is_superuser && !collection))
      return;
    setSubmitting(true);
    try {
      const accepted = await selectArchiveEntries(manifest.archive_id, {
        names: [...selected],
        collection: collection ?? undefined,
        tags: tags.length ? tags.join(",") : undefined,
      });
      trackImportJob(
        accepted.job_id,
        uiMessage("Import {value1}", { value1: manifest.archive_name }),
      );
      if (task) updateTask(task.id, { archiveReviewDone: true });
      toast.info(uiText("Selected ZIP files are importing in the background."));
      onClose();
    } catch (error) {
      toast.error(error);
      setSubmitting(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title={uiText("Choose ZIP files")}
      className="flex max-h-[90dvh] max-w-5xl flex-col"
    >
      <div className="flex min-h-0 flex-col gap-4">
        <p className="text-sm text-muted-foreground">
          {manifest
            ? uiText(
                "Choose the files to add from {value1}. Your selection stays while you browse.",
                {
                  value1: manifest.archive_name,
                },
              )
            : uiText("Loading ZIP files…")}
        </p>
        {loadError && (
          <div className="flex flex-col gap-3">
            <p role="alert" className="text-sm text-destructive">
              {loadError}
            </p>
            <Button variant="outline" className="self-end" onClick={onClose}>
              {uiText("Cancel")}
            </Button>
          </div>
        )}
        {manifest && (
          <>
            <div className="flex items-center gap-2">
              <Search className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
              <Input
                autoFocus
                aria-label={uiText("Search ZIP files")}
                placeholder={uiText("Search ZIP files")}
                value={query}
                maxLength={128}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
            <nav
              aria-label={uiText("Browse ZIP folders")}
              className="flex flex-wrap items-center gap-1 text-sm"
            >
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setFolder(null)}
                aria-current={folder === null ? "page" : undefined}
              >
                {uiText("ZIP root")}
              </Button>
              {breadcrumbs.map((path) => (
                <span key={path} className="flex min-w-0 items-center gap-1">
                  <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-auto whitespace-normal break-words text-left"
                    onClick={() => setFolder(path)}
                    aria-current={folder === path ? "page" : undefined}
                  >
                    {path.split("/").at(-1)}
                  </Button>
                </span>
              ))}
            </nav>
            <div className="min-h-0 overflow-y-auto overscroll-contain pr-1">
              {!search && folders.length > 0 && (
                <ul
                  aria-label={uiText("Browse ZIP folders")}
                  className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3"
                >
                  {folders.map((path) => (
                    <li key={path}>
                      <Button
                        variant="outline"
                        className="h-auto min-h-11 w-full justify-start whitespace-normal break-words text-left"
                        onClick={() => setFolder(path)}
                      >
                        <Folder className="mr-2 h-4 w-4 shrink-0" aria-hidden />
                        {path.split("/").at(-1)}
                      </Button>
                    </li>
                  ))}
                </ul>
              )}
              {visibleFiles.length === 0 && (search || folders.length === 0) && (
                <p className="py-8 text-sm text-muted-foreground">
                  {importable.length === 0
                    ? uiText("No importable 3D files in this archive.")
                    : uiText("No ZIP files match here.")}
                </p>
              )}
              <ul
                aria-label={uiText("ZIP files")}
                className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4"
              >
                {visibleFiles.map((entry) => {
                  const checked = selected.has(entry.name);
                  return (
                    <li key={entry.name} className="min-w-0">
                      <button
                        type="button"
                        aria-pressed={checked}
                        aria-label={entry.name}
                        onClick={() => toggle(entry.name)}
                        className={`relative flex h-full w-full flex-col gap-2 rounded-lg border p-3 text-left transition-transform duration-press active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${checked ? "border-primary bg-accent text-accent-foreground" : "border-border bg-card text-card-foreground hover:bg-muted"}`}
                      >
                        <span className="flex w-full items-start justify-between gap-2">
                          <File className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                          {checked ? (
                            <CheckSquare className="h-5 w-5 shrink-0" aria-hidden />
                          ) : (
                            <Square className="h-5 w-5 shrink-0" aria-hidden />
                          )}
                        </span>
                        <span className="break-words text-sm font-medium">
                          {search ? entry.name : segments(entry).at(-1)}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {entry.file_type?.toUpperCase()} · {formatBytes(entry.size_bytes)}
                        </span>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="flex flex-wrap items-end justify-between gap-3 border-t border-border pt-3">
              <p role="status" className="text-sm text-muted-foreground">
                {uiText("{value1} selected", { value1: String(selected.size) })}
              </p>
              <div className="flex flex-wrap items-end gap-2">
                <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {uiText("Destination collection")}
                  <select
                    value={collection ?? ""}
                    onChange={(event) => setPickedCollection(event.target.value || null)}
                    className="min-h-9 rounded border border-border bg-background px-2 text-sm text-foreground"
                  >
                    <option value="">
                      {user?.is_superuser ? uiText("Vault root") : uiText("Choose a collection")}
                    </option>
                    {writableCollections.map((item) => (
                      <option key={item.id} value={item.path}>
                        {collectionDisplayPath(collections, item.path)}
                      </option>
                    ))}
                  </select>
                </label>
                <Button variant="outline" onClick={onClose}>
                  {uiText("Cancel")}
                </Button>
                <Button
                  disabled={
                    selected.size === 0 || submitting || (!user?.is_superuser && !collection)
                  }
                  onClick={() => void importSelected()}
                >
                  {submitting
                    ? uiText("Importing…")
                    : uiText("Import {value1} selected", { value1: String(selected.size) })}
                </Button>
              </div>
            </div>
          </>
        )}
      </div>
    </Modal>
  );
}
