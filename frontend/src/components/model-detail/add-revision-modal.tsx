"use client";

import { useRef, useState } from "react";
import type { DragEvent as ReactDragEvent, FormEvent } from "react";
import { FileCode2, Plus, Star, Upload, X } from "lucide-react";

import { addGcodeRevision } from "@/lib/api";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { formatBytes } from "@/lib/format";
import { ModelRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { Localized } from "@/components/ui/localized";

const GCODE_ACCEPT = ".gcode,.g,.gco";

function isGcodeFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return [".gcode", ".g", ".gco"].some((extension) => name.endsWith(extension));
}

export function AddGcodeRevisionModal({
  modelId,
  onClose,
  onUploaded,
}: {
  modelId: number;
  onClose: () => void;
  onUploaded: (model: ModelRead) => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [recommended, setRecommended] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function selectFile(nextFile: File | null) {
    if (nextFile && !isGcodeFile(nextFile)) {
      setFile(null);
      setError("Choose a .gcode, .g, or .gco file.");
      return;
    }
    setFile(nextFile);
    setError(null);
  }

  function onDrop(event: ReactDragEvent<HTMLElement>) {
    event.preventDefault();
    setDragActive(false);
    selectFile(event.dataTransfer.files[0] ?? null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (!file || submitting) return;
    const taskId = createTask({
      title: `Upload revision ${file.name}`,
      detail: "Uploading G-code revision",
      status: "running",
      progress: 20,
    });
    setSubmitting(true);
    setError(null);
    try {
      const form = new FormData();
      form.append("file", file);
      if (label.trim()) form.append("revision_label", label.trim());
      if (notes.trim()) form.append("revision_notes", notes.trim());
      form.append("revision_status", "needs_test");
      form.append("is_recommended", String(recommended));
      updateTask(taskId, {
        detail: "Adding revision to model",
        status: "running",
        progress: 70,
      });
      onUploaded(await addGcodeRevision(modelId, form));
      updateTask(taskId, {
        detail: "Revision uploaded",
        status: "completed",
        progress: 100,
      });
    } catch (e: any) {
      setError(e.message);
      updateTask(taskId, {
        detail: e.message || "Revision upload failed",
        status: "failed",
        progress: 100,
      });
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Localized><Modal
      open
      onClose={() => { if (!submitting) onClose(); }}
      title="Add G-code revision"
      className="max-w-lg"
    >
      <form onSubmit={submit} className="space-y-5">
        <p className="-mt-2 text-sm leading-relaxed text-muted-foreground">
          Upload another slice while keeping earlier settings and print history available.
        </p>

        <input
          ref={fileInputRef}
          type="file"
          accept={GCODE_ACCEPT}
          onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          className="sr-only"
        />

        <div className="space-y-1.5">
          <span className="text-sm font-medium text-foreground">G-code file</span>
          {file ? (
            <div
              onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
              className={cn(
                "flex items-center gap-3 rounded-lg border bg-muted/40 p-3 transition-[background-color,border-color] duration-press",
                dragActive && "border-primary bg-primary-soft",
              )}
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
                <FileCode2 className="h-5 w-5" />
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium text-foreground">{file.name}</p>
                <p className="mt-0.5 font-mono text-2xs uppercase tracking-wider text-muted-foreground">
                  {formatBytes(file.size)} · G-code
                </p>
              </div>
              <Button type="button" variant="ghost" size="xs" onClick={() => fileInputRef.current?.click()}>
                Replace
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                onClick={() => { selectFile(null); if (fileInputRef.current) fileInputRef.current.value = ""; }}
                aria-label="Remove selected file"
              >
                <X className="h-4 w-4" />
              </Button>
            </div>
          ) : (
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              onDragEnter={(event) => { event.preventDefault(); setDragActive(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragActive(false)}
              onDrop={onDrop}
              className={cn(
                "flex w-full flex-col items-center justify-center rounded-lg border border-dashed border-border bg-muted/30 px-6 py-7 text-center transition-[background-color,border-color,transform] duration-press active:scale-[0.99] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                dragActive ? "border-primary bg-primary-soft" : "hover:border-primary/60 hover:bg-muted/60",
              )}
            >
              <span className="mb-3 flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
                <Upload className="h-5 w-5" />
              </span>
              <span className="text-sm font-medium text-foreground">Choose G-code or drop it here</span>
              <span className="mt-1 text-xs text-muted-foreground">.gcode, .g, or .gco</span>
            </button>
          )}
        </div>

        {error && (
          <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive">
            {error}
          </div>
        )}

        <div className="grid gap-4">
          <label className="space-y-1.5 text-sm font-medium text-foreground">
            Revision label <span className="font-normal text-muted-foreground">Optional</span>
            <Input
              value={label}
              onChange={(event) => setLabel(event.target.value)}
              maxLength={128}
              placeholder="e.g. Stronger walls"
            />
          </label>
          <label className="space-y-1.5 text-sm font-medium text-foreground">
            Notes <span className="font-normal text-muted-foreground">Optional</span>
            <textarea
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              rows={3}
              placeholder="What changed in this slice?"
              className={cn(inputClasses, "h-auto resize-none")}
            />
          </label>
        </div>

        <div
          className={cn(
            "flex items-start gap-3 rounded-lg border p-3 transition-[background-color,border-color] duration-press",
            recommended ? "border-primary bg-primary-soft" : "border-border bg-background",
          )}
        >
          <Checkbox
            checked={recommended}
            onChange={setRecommended}
            disabled={submitting}
            ariaLabel="Mark as recommended"
            className="mt-0.5"
          />
          <Star className={cn("mt-0.5 h-4 w-4 shrink-0 text-muted-foreground", recommended && "fill-current text-primary")} />
          <div>
            <p className="text-sm font-medium text-foreground">Mark as recommended</p>
            <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
              Makes this default revision for downloads and printer sends. Current recommendation will be replaced.
            </p>
          </div>
        </div>

        <div className="-mx-6 -mb-6 flex justify-end gap-2 border-t border-border bg-muted/30 px-6 py-4">
          <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>Cancel</Button>
          <Button type="submit" loading={submitting} disabled={!file}>
            {!submitting && <Plus className="h-4 w-4" />}
            {submitting ? "Adding revision…" : "Add revision"}
          </Button>
        </div>
      </form>
    </Modal></Localized>
  );
}
