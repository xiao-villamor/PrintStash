"use client";

import { useBlocker } from "@tanstack/react-router";
import { FileCog, Folder, FolderOpen, LockKeyhole, RefreshCcw } from "lucide-react";
import { type CSSProperties, useEffect, useId, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Skeleton } from "@/components/ui/skeleton";
import { applyConfigTextEdit, textareaConfigText } from "./config-text";
import {
  listConfigFiles,
  readConfigFile,
  saveConfigFile,
  type PrinterConfigFile,
  type PrinterConfigFileEntry,
} from "@/lib/api/printer-config-files";
import { parseApiError } from "@/lib/errors";
import { useUiLocale } from "@/lib/i18n";
import { uiText } from "@/lib/locale";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import "./config-editor.css";

const MOONRAKER_CONFIG_ROOT = "config";
const CONFIG_ENCODING = "UTF-8";

interface ConfigTree {
  folders: Map<string, ConfigTree>;
  files: PrinterConfigFileEntry[];
}

function treeFromFiles(files: PrinterConfigFileEntry[]): ConfigTree {
  const root: ConfigTree = { folders: new Map(), files: [] };
  for (const file of files) {
    let folder = root;
    for (const name of file.path.split("/").slice(0, -1)) {
      let child = folder.folders.get(name);
      if (!child) {
        child = { folders: new Map(), files: [] };
        folder.folders.set(name, child);
      }
      folder = child;
    }
    folder.files.push(file);
  }
  return root;
}

function FileTree({
  tree,
  selected,
  select,
  disabled,
}: {
  tree: ConfigTree;
  selected: string | null;
  select: (path: string) => void;
  disabled: boolean;
}) {
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  return (
    <ul className="space-y-0.5">
      {[...tree.folders].map(([name, child]) => (
        <li key={name}>
          <Button
            variant="ghost"
            size="sm"
            aria-expanded={!collapsed.has(name)}
            className="w-full justify-start gap-2"
            onClick={() =>
              setCollapsed((previous) => {
                const next = new Set(previous);
                if (next.has(name)) next.delete(name);
                else next.add(name);
                return next;
              })
            }
          >
            {collapsed.has(name) ? (
              <Folder aria-hidden className="size-4 shrink-0" />
            ) : (
              <FolderOpen aria-hidden className="size-4 shrink-0" />
            )}
            <span className="truncate">{name}</span>
          </Button>
          <div hidden={collapsed.has(name)} className="ml-3 border-l pl-2">
            <FileTree tree={child} selected={selected} select={select} disabled={disabled} />
          </div>
        </li>
      ))}
      {tree.files.map((file) => (
        <li key={file.path}>
          <Button
            variant="ghost"
            size="sm"
            aria-current={file.path === selected ? "true" : undefined}
            disabled={disabled}
            className="w-full justify-start gap-2 overflow-hidden aria-[current=true]:bg-foreground/10 aria-[current=true]:text-foreground"
            title={file.path}
            onClick={() => select(file.path)}
          >
            <FileCog aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate">{file.path.split("/").at(-1)}</span>
            {!file.writable && <LockKeyhole aria-hidden className="ml-auto size-3 shrink-0" />}
          </Button>
        </li>
      ))}
    </ul>
  );
}

interface ConfigDraft {
  file: PrinterConfigFile;
  text: string;
}

type SaveNotice = "saved" | "conflict" | "unconfirmed" | "read-only" | "too-large" | "failed";

function noticeText(notice: SaveNotice) {
  switch (notice) {
    case "saved":
      return uiText("Saved to the printer");
    case "conflict":
      return uiText("This file changed on the printer. Review the current file before saving.");
    case "unconfirmed":
      return uiText("Save could not be confirmed. Read the printer file before trying again.");
    case "read-only":
      return uiText("Moonraker refused to write this file.");
    case "too-large":
      return uiText("This configuration file exceeds the editor size limit.");
    case "failed":
      return uiText("Could not save this configuration file.");
  }
}

function readErrorText(error: string) {
  switch (error) {
    case "printer_config_not_text":
      return uiText("This file is not valid UTF-8 configuration text.");
    case "printer_config_file_not_found":
      return uiText("This file is no longer available on the printer.");
    case "printer_config_too_large":
      return uiText("This configuration file exceeds the editor size limit.");
    default:
      return uiText("Could not read the configuration file.");
  }
}

export function PrinterConfigEditor({ printerId }: { printerId: number }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const remembered = searchParams.get("file");
  const [files, setFiles] = useState<PrinterConfigFileEntry[] | null>(null);
  const [listError, setListError] = useState(false);
  const [listVersion, setListVersion] = useState(0);
  const defaultPath = files?.find((file) => file.path === "printer.cfg")?.path;

  function fileUrl(path: string) {
    const next = new URLSearchParams(searchParams);
    next.set("file", path);
    return `${pathname}?${next}`;
  }

  const defaultUrl = remembered === null && defaultPath ? fileUrl(defaultPath) : null;
  useEffect(() => {
    if (defaultUrl !== null) router.replace(defaultUrl, { resetScroll: false });
  }, [defaultUrl, router]);

  useEffect(() => {
    const abort = new AbortController();
    listConfigFiles(printerId, abort.signal)
      .then(({ files: next }) => {
        if (!abort.signal.aborted) setFiles(next);
      })
      .catch(() => {
        if (!abort.signal.aborted) setListError(true);
      });
    return () => abort.abort();
  }, [printerId, listVersion]);

  return (
    <ConfigFileWorkspace
      key={JSON.stringify([printerId, remembered])}
      printerId={printerId}
      selected={remembered}
      files={files}
      listError={listError}
      defaultPending={defaultUrl !== null}
      onSelect={(path) => {
        if (path !== remembered) router.push(fileUrl(path), { resetScroll: false });
      }}
      onRefresh={() => {
        setListError(false);
        setListVersion((version) => version + 1);
      }}
    />
  );
}

function ConfigFileWorkspace({
  printerId,
  selected,
  files,
  listError,
  defaultPending,
  onSelect,
  onRefresh,
}: {
  printerId: number;
  selected: string | null;
  files: PrinterConfigFileEntry[] | null;
  listError: boolean;
  defaultPending: boolean;
  onSelect: (path: string) => void;
  onRefresh: () => void;
}) {
  useUiLocale();
  const editorId = useId();
  const statusId = useId();
  const gutter = useRef<HTMLPreElement>(null);
  const [draft, setDraft] = useState<ConfigDraft | null>(null);
  const [readError, setReadError] = useState<string | null>(null);
  const [readVersion, setReadVersion] = useState(0);
  const [notice, setNotice] = useState<SaveNotice | null>(null);
  const [reviewed, setReviewed] = useState<PrinterConfigFile | null>(null);
  const [reviewError, setReviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "review" | null>(null);
  const dirty = draft !== null && draft.text !== draft.file.content;
  const needsReview = notice === "conflict" || notice === "unconfirmed";
  const blockDraft = dirty || busy === "save";

  const blocker = useBlocker({
    shouldBlockFn: () => blockDraft,
    enableBeforeUnload: blockDraft,
    disabled: !blockDraft,
    withResolver: true,
  });

  useEffect(() => {
    if (selected === null) return;
    const abort = new AbortController();
    readConfigFile(printerId, selected, abort.signal)
      .then((file) => {
        if (abort.signal.aborted) return;
        if (file.path !== selected) {
          setReadError("printer_config_unexpected_path");
          return;
        }
        setDraft({ file, text: file.content });
      })
      .catch((error) => {
        if (!abort.signal.aborted) setReadError(parseApiError(error).code);
      });
    return () => abort.abort();
  }, [printerId, selected, readVersion]);

  const tree = useMemo(() => (files ? treeFromFiles(files) : null), [files]);
  const text = draft ? textareaConfigText(draft.text) : "";
  const lineCount = text.split("\n").length;
  const editorStyle: CSSProperties & { "--printer-config-lines": number } = {
    "--printer-config-lines": lineCount,
  };
  const lineNumbers = useMemo(
    () => Array.from({ length: lineCount }, (_, i) => i + 1).join("\n"),
    [lineCount],
  );

  const ready = draft?.file.path === selected ? draft : null;
  const canSave = ready?.file.writable === true && dirty && !busy && !needsReview;

  async function save() {
    if (!canSave || !ready) return;
    setBusy("save");
    setNotice(null);
    try {
      const file = await saveConfigFile(printerId, ready.file, ready.text);
      if (file.path !== selected) {
        setNotice("unconfirmed");
        return;
      }
      setDraft({ file, text: file.content });
      setNotice("saved");
    } catch (error) {
      const { code, status } = parseApiError(error);
      if (code === "printer_config_conflict") setNotice("conflict");
      else if (status >= 500 || status === 0) setNotice("unconfirmed");
      else if (code === "printer_config_read_only") {
        setDraft({ ...ready, file: { ...ready.file, writable: false } });
        setNotice("read-only");
      } else if (status === 413) setNotice("too-large");
      else setNotice("failed");
    } finally {
      setBusy(null);
    }
  }

  async function review() {
    if (!ready || busy) return;
    setBusy("review");
    setReviewError(null);
    try {
      const file = await readConfigFile(printerId, ready.file.path);
      if (file.path !== selected) {
        setReviewError("printer_config_unexpected_path");
        return;
      }
      if (file.content === ready.text) {
        setDraft({ file, text: file.content });
        setNotice("saved");
      } else setReviewed(file);
    } catch (error) {
      setReviewError(parseApiError(error).code);
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className="overflow-hidden">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <FileCog aria-hidden className="size-4 text-muted-foreground" />
          <h2 className="text-sm font-semibold">{uiText("Configuration files")}</h2>
        </div>
        <Button variant="outline" size="sm" onClick={onRefresh}>
          <RefreshCcw aria-hidden className="size-4" />
          {uiText("Refresh files")}
        </Button>
      </header>
      <div className="printer-config-workspace">
        <nav
          aria-label={uiText("Configuration file browser")}
          className="printer-config-files bg-muted/25 p-3"
        >
          <p className="mb-2 px-2 font-mono text-xs font-semibold">{MOONRAKER_CONFIG_ROOT}</p>
          {listError && (
            <p role="alert" className="px-2 text-sm text-destructive">
              {uiText("Could not list configuration files.")}
            </p>
          )}
          {tree && (
            <FileTree tree={tree} selected={selected} disabled={Boolean(busy)} select={onSelect} />
          )}
          {files === null && !listError && <Skeleton className="h-20" />}
          {files?.length === 0 && (
            <p className="px-2 text-sm text-muted-foreground">
              {uiText("No configuration files available.")}
            </p>
          )}
        </nav>
        <section className="min-w-0 px-4 py-3">
          {!ready && selected !== null && (
            <p className="break-all pb-2 font-mono text-sm font-semibold">{selected}</p>
          )}
          {ready ? (
            <>
              <div className="flex flex-wrap items-baseline justify-between gap-2 pb-2">
                <label className="break-all font-mono text-sm font-semibold" htmlFor={editorId}>
                  {ready.file.path}
                  <span className="sr-only"> {uiText("Configuration text")}</span>
                </label>
                <p id={statusId} role="status" className="text-sm text-muted-foreground">
                  {busy === "save"
                    ? uiText("Saving file…")
                    : dirty
                      ? uiText("Unsaved edits")
                      : ready.file.writable
                        ? notice === "saved"
                          ? noticeText(notice)
                          : uiText("Saved")
                        : uiText("Read-only")}
                </p>
              </div>
              <div
                className="printer-config-editor rounded-lg border bg-field-surface font-mono text-sm focus-within:ring-2 focus-within:ring-ring"
                style={editorStyle}
              >
                <div
                  aria-hidden
                  className="printer-config-gutter select-none border-r bg-muted/40 text-muted-foreground"
                >
                  <pre ref={gutter}>{lineNumbers}</pre>
                </div>
                <textarea
                  id={editorId}
                  aria-describedby={statusId}
                  className="bg-transparent outline-none"
                  value={text}
                  wrap="off"
                  spellCheck={false}
                  autoCapitalize="off"
                  readOnly={!ready.file.writable || Boolean(busy)}
                  onScroll={(event) => {
                    if (gutter.current) {
                      gutter.current.style.transform = `translateY(${-event.currentTarget.scrollTop}px)`;
                    }
                  }}
                  onChange={(event) =>
                    setDraft({
                      file: ready.file,
                      text: applyConfigTextEdit(ready.text, event.currentTarget.value),
                    })
                  }
                  onKeyDown={(event) => {
                    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                      event.preventDefault();
                      void save();
                    }
                  }}
                />
              </div>
              {notice && notice !== "saved" && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  {noticeText(notice)}
                </p>
              )}
              {reviewError && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                  {readErrorText(reviewError)}
                </p>
              )}
              {reviewed && (
                <section className="mt-3 space-y-2 rounded-lg border p-3">
                  <h3 className="text-sm font-semibold">{uiText("Current printer file")}</h3>
                  <pre className="max-h-40 overflow-auto whitespace-pre rounded-md border bg-muted/25 p-2 font-mono text-sm">
                    {reviewed.content}
                  </pre>
                  <div className="flex flex-wrap gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setDraft({ file: reviewed, text: reviewed.content });
                        setReviewed(null);
                        setNotice(null);
                      }}
                    >
                      {uiText("Use printer version")}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!reviewed.writable}
                      onClick={() => {
                        setDraft({ file: reviewed, text: ready.text });
                        setReviewed(null);
                        setNotice(null);
                      }}
                    >
                      {uiText("Keep draft against current revision")}
                    </Button>
                  </div>
                </section>
              )}
              <footer className="flex flex-wrap items-center justify-between gap-2 pt-3">
                <span className="text-xs text-muted-foreground">{CONFIG_ENCODING}</span>
                <div className="flex flex-wrap gap-2">
                  {needsReview && !reviewed && (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={Boolean(busy)}
                      onClick={() => void review()}
                    >
                      {busy === "review" ? uiText("Reading file…") : uiText("Review printer file")}
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!dirty || Boolean(busy) || needsReview}
                    onClick={() => {
                      setDraft({ file: ready.file, text: ready.file.content });
                      setNotice(null);
                    }}
                  >
                    {uiText("Revert edits")}
                  </Button>
                  <Button size="sm" disabled={!canSave} onClick={() => void save()}>
                    {uiText("Save file")}
                  </Button>
                </div>
              </footer>
            </>
          ) : readError ? (
            <div className="space-y-2">
              <p role="alert" className="text-sm text-destructive">
                {readErrorText(readError)} {uiText("Choose a configuration file.")}
              </p>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setReadError(null);
                  setReadVersion((version) => version + 1);
                }}
              >
                {uiText("Retry file")}
              </Button>
            </div>
          ) : selected !== null || defaultPending || (files === null && !listError) ? (
            <Skeleton aria-label={uiText("Reading file…")} className="printer-config-loading" />
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">
              {uiText("Choose a configuration file.")}
            </p>
          )}
        </section>
      </div>
      <ConfirmModal
        open={blocker.status === "blocked"}
        busy={busy === "save"}
        title={uiText("Discard unsaved edits?")}
        description={uiText("Leaving this file discards your local draft.")}
        confirmLabel={uiText("Discard edits")}
        onClose={() => blocker.reset?.()}
        onConfirm={() => blocker.proceed?.()}
      />
    </Card>
  );
}
