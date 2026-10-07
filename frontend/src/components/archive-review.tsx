"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckSquare, ChevronRight, File, Folder, Search, Square } from "lucide-react";

import { getJobStatus } from "@/lib/api/jobs";
import { selectArchiveEntries } from "@/lib/api/models";
import { useAuth } from "@/lib/auth-context";
import { ApiError, userMessage } from "@/lib/errors";
import { onAuthChange } from "@/lib/auth-store";
import { getSessionVersion, requireSessionVersion } from "@/lib/session-transport";
import { formatBytes } from "@/lib/format";
import { useUiLocale } from "@/lib/i18n";
import { uiMessage, uiText } from "@/lib/locale";
import { useCollectionLookup, useCollectionSearch } from "@/lib/queries";
import { listTasks, trackImportJob, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import type { ArchiveEntry, ArchiveManifest, CollectionNodeRead, JobStatus } from "@/types";

import { Button } from "@/components/ui/button";
import { DropdownMenu } from "@/components/ui/dropdown-menu";
import { CollectionPicker } from "@/components/collection-picker";
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
  const [mountedSession] = useState(getSessionVersion);
  const session = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  if (session !== mountedSession) return null;
  return <ArchiveReview key={jobId} jobId={jobId} onClose={onClose} session={session} />;
}

function ArchiveReview({
  jobId,
  onClose,
  session,
}: {
  jobId: string;
  onClose: () => void;
  session: number;
}) {
  useUiLocale();
  const { user } = useAuth();
  // The first folder a non-administrator may write to, found without listing
  // the library (#295).
  const writableProbe = useCollectionSearch("", "edit", { enabled: !user?.is_superuser });
  const firstWritable = writableProbe.data?.pages[0]?.items[0] ?? null;
  // A completed archive manifest is immutable review input, not a live Job poll.
  const manifestQuery = useQuery({
    queryKey: ["archive-manifest", session, jobId],
    queryFn: async ({ signal }) => {
      requireSessionVersion(session);
      return manifestFromJob(await getJobStatus(jobId, { signal }));
    },
    staleTime: Infinity,
    retry: false,
  });
  const denied =
    manifestQuery.error instanceof ApiError && [401, 403, 404].includes(manifestQuery.error.status);
  const manifest = denied ? null : (manifestQuery.data ?? null);
  const loadError =
    manifestQuery.error instanceof ArchiveReviewUnavailableError
      ? manifestQuery.error.message
      : manifestQuery.error
        ? userMessage(manifestQuery.error)
        : null;
  const live = useRef(true);
  useEffect(() => {
    live.current = true;
    return () => {
      live.current = false;
    };
  }, []);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [folder, setFolder] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  // The collection picked, "none" for the vault root, or undefined for no pick yet.
  const [pickedCollection, setPickedCollection] = useState<CollectionNodeRead | "none" | undefined>(
    undefined,
  );
  const [pickerOpen, setPickerOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const task = listTasks().find((item) => item.jobId === jobId);
  const collection: string | null =
    pickedCollection !== undefined
      ? pickedCollection === "none"
        ? null
        : pickedCollection.path
      : (task?.archiveCollection ?? (!user?.is_superuser ? (firstWritable?.path ?? null) : null));
  // A destination that was not picked here still needs its names.
  const destinationLookup = useCollectionLookup(pickedCollection === undefined ? collection : null);
  const destinationLabel =
    pickedCollection !== undefined && pickedCollection !== "none"
      ? pickedCollection.display_path
      : destinationLookup.data?.collection.path === collection
        ? destinationLookup.data.collection.display_path
        : null;
  const tags = task?.archiveTags ?? [];

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

  function filesInFolder(path: string): ArchiveEntry[] {
    return importable.filter((entry) => entry.name.startsWith(`${path}/`));
  }

  function toggleFolder(path: string) {
    const names = filesInFolder(path).map((entry) => entry.name);
    setSelected((current) => {
      const next = new Set(current);
      const allSelected = names.every((name) => next.has(name));
      for (const name of names) {
        if (allSelected) next.delete(name);
        else next.add(name);
      }
      return next;
    });
  }

  async function importSelected() {
    if (!manifest || selected.size === 0 || submitting || (!user?.is_superuser && !collection))
      return;
    setSubmitting(true);
    try {
      if (!live.current) return;
      requireSessionVersion(session);
      const accepted = await selectArchiveEntries(manifest.archive_id, {
        names: [...selected],
        collection: collection ?? undefined,
        tags: tags.length ? tags.join(",") : undefined,
      });
      requireSessionVersion(session);
      if (!live.current) return;
      trackImportJob(
        accepted.job_id,
        uiMessage("Import {value1}", { value1: manifest.archive_name }),
      );
      if (task) updateTask(task.id, { archiveReviewDone: true });
      toast.info(uiText("Selected ZIP files are importing in the background."));
      onClose();
    } catch (error) {
      if (!live.current || session !== getSessionVersion()) return;
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
            <Button
              variant="outline"
              className="self-end"
              onClick={() => void manifestQuery.refetch()}
              disabled={manifestQuery.isFetching}
            >
              {uiText("Retry")}
            </Button>
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
            <div className="flex flex-wrap items-center justify-between gap-2 rounded border border-border bg-muted/30 px-3 py-2">
              <p className="text-xs text-muted-foreground">
                {uiText("{value1} importable files in this ZIP", {
                  value1: String(importable.length),
                })}
              </p>
              <div className="flex flex-wrap items-center gap-1">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={importable.length === 0 || selected.size === importable.length}
                  onClick={() => setSelected(new Set(importable.map((entry) => entry.name)))}
                >
                  {uiText("Select all {value1} ZIP files", { value1: String(importable.length) })}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  disabled={selected.size === 0}
                  onClick={() => setSelected(new Set())}
                >
                  {uiText("Clear selection")}
                </Button>
              </div>
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
              {visibleFiles.length === 0 && (search || folders.length === 0) && (
                <p className="py-8 text-sm text-muted-foreground">
                  {importable.length === 0
                    ? uiText("No importable 3D files in this archive.")
                    : uiText("No ZIP files match here.")}
                </p>
              )}
              <ul
                aria-label={uiText("ZIP contents")}
                className="grid grid-cols-1 gap-2 sm:grid-cols-2 md:grid-cols-3"
              >
                {!search &&
                  folders.map((path) => {
                    const children = filesInFolder(path);
                    const selectedCount = children.filter((entry) =>
                      selected.has(entry.name),
                    ).length;
                    const allSelected = selectedCount === children.length;
                    const name = path.slice(path.lastIndexOf("/") + 1);
                    return (
                      <li key={path} className="min-w-0">
                        <div
                          className={`flex h-28 flex-col justify-between gap-2 rounded-lg border p-3 ${allSelected ? "border-primary bg-accent text-accent-foreground" : selectedCount > 0 ? "border-border bg-accent/50 text-accent-foreground" : "border-border bg-card text-card-foreground"}`}
                        >
                          <div className="flex min-w-0 items-start gap-2">
                            <Button
                              variant="ghost"
                              className="h-auto min-w-0 flex-1 justify-start gap-2 whitespace-normal p-0 text-left hover:bg-transparent"
                              aria-label={uiText("Open folder {value1}", { value1: name })}
                              title={path}
                              onClick={() => setFolder(path)}
                            >
                              <Folder
                                className="h-5 w-5 shrink-0 text-muted-foreground"
                                aria-hidden
                              />
                              <span className="line-clamp-2 break-words text-sm font-medium">
                                {name}
                              </span>
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 shrink-0"
                              aria-pressed={allSelected}
                              aria-label={uiText(
                                allSelected ? "Deselect folder {value1}" : "Select folder {value1}",
                                { value1: name },
                              )}
                              onClick={() => toggleFolder(path)}
                            >
                              {allSelected ? (
                                <CheckSquare className="h-5 w-5" aria-hidden />
                              ) : (
                                <Square className="h-5 w-5" aria-hidden />
                              )}
                            </Button>
                          </div>
                          <p className="text-xs text-muted-foreground">
                            {uiText("{value1} of {value2} files selected", {
                              value1: String(selectedCount),
                              value2: String(children.length),
                            })}
                          </p>
                        </div>
                      </li>
                    );
                  })}
                {visibleFiles.map((entry) => {
                  const checked = selected.has(entry.name);
                  return (
                    <li key={entry.name} className="min-w-0">
                      <button
                        type="button"
                        aria-pressed={checked}
                        aria-label={entry.name}
                        onClick={() => toggle(entry.name)}
                        className={`flex h-28 w-full flex-col justify-between gap-2 rounded-lg border p-3 text-left transition-transform duration-press active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${checked ? "border-primary bg-accent text-accent-foreground" : "border-border bg-card text-card-foreground hover:bg-muted"}`}
                      >
                        <span className="flex w-full items-start justify-between gap-2">
                          <File className="h-5 w-5 shrink-0 text-muted-foreground" aria-hidden />
                          {checked ? (
                            <CheckSquare className="h-5 w-5 shrink-0" aria-hidden />
                          ) : (
                            <Square className="h-5 w-5 shrink-0" aria-hidden />
                          )}
                        </span>
                        <span
                          className="line-clamp-2 break-words text-sm font-medium"
                          title={entry.name}
                        >
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
                {uiText("{value1} of {value2} files selected", {
                  value1: String(selected.size),
                  value2: String(importable.length),
                })}
              </p>
              <div className="flex flex-wrap items-end gap-2">
                <div className="flex flex-col gap-1 text-xs text-muted-foreground">
                  {uiText("Destination collection")}
                  <DropdownMenu
                    open={pickerOpen}
                    onOpenChange={setPickerOpen}
                    align="end"
                    role="dialog"
                    contentClassName="w-72 rounded border border-border bg-popover p-2 text-popover-foreground shadow-lg"
                    trigger={
                      <button
                        type="button"
                        aria-haspopup="dialog"
                        aria-expanded={pickerOpen}
                        onClick={() => setPickerOpen((value) => !value)}
                        className="min-h-9 max-w-56 truncate rounded border border-border bg-background px-2 text-left text-sm text-foreground"
                      >
                        {destinationLabel ??
                          (collection === null
                            ? user?.is_superuser
                              ? uiText("Vault root")
                              : uiText("Choose a collection")
                            : collection)}
                      </button>
                    }
                  >
                    <CollectionPicker
                      minRole="edit"
                      selectedPath={collection ?? ""}
                      onSelect={(picked) => {
                        setPickedCollection(picked ?? "none");
                        setPickerOpen(false);
                      }}
                      noneLabel={user?.is_superuser ? uiText("Vault root") : undefined}
                      emptyLabel={uiText("No editable collections.")}
                    />
                  </DropdownMenu>
                </div>
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
