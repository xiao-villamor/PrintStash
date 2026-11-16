"use client";

import { uiMessage } from "@/lib/locale";
import { userMessage } from "@/lib/errors";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useRef, useState } from "react";
import type { DragEvent as ReactDragEvent, FormEvent } from "react";
import { FileCode2, Upload, X } from "lucide-react";

import { addGcodeRevision } from "@/lib/api";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { formatBytes } from "@/lib/format";
import { ModelRead } from "@/types";
import { ChoiceField } from "./choice-field";
import { Button } from "@/components/ui/button";
import { Input, inputClasses } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { Localized } from "@/components/ui/localized";
import { GCODE_ACCEPT, isGcodeFile } from "@/lib/bulk-upload";

export function AddGcodeRevisionModal({
  modelId,
  modelName,
  currentRecommendedRevision,
  onClose,
  onUploaded,
}: {
  modelId: number;
  modelName?: string;
  currentRecommendedRevision?: number;
  onClose: () => void;
  onUploaded: (model: ModelRead) => void;
}) {
  useUiLocale();
  const formId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [label, setLabel] = useState("");
  const [notes, setNotes] = useState("");
  const [recommended, setRecommended] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  function selectFile(nextFile: File | null) {
    if (nextFile && !isGcodeFile(nextFile.name)) {
      setFile(null);
      setError(uiText("Choose a .gcode, .g, .gco, or .bgcode file."));
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
      title: uiMessage("Upload revision {value1}", { value1: String(file.name) }),
      detail: uiMessage("Uploading G-code revision"),
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
        detail: uiMessage("Adding revision to model"),
        status: "running",
        progress: 70,
      });
      onUploaded(await addGcodeRevision(modelId, form));
      updateTask(taskId, {
        detail: uiMessage("Revision uploaded"),
        status: "completed",
        progress: 100,
      });
    } catch (e: any) {
      setError(userMessage(e));
      updateTask(taskId, {
        detail: e.message || uiMessage("Revision upload failed"),
        status: "failed",
        progress: 100,
      });
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Localized>
      <Modal
        open
        onClose={() => {
          if (!submitting) onClose();
        }}
        title={uiText("Add G-code revision")}
        description={uiText(
          "Add a file produced by your slicer. Earlier Revisions and their print history remain available.",
        )}
        footer={
          <>
            <Button type="button" variant="outline" onClick={onClose} disabled={submitting}>
              {uiText("Cancel")}
            </Button>
            <Button type="submit" form={formId} loading={submitting} disabled={!file}>
              {submitting ? uiText("Adding revision…") : uiText("Add revision")}
            </Button>
          </>
        }
      >
        <form id={formId} onSubmit={submit} className="space-y-4">
          {modelName && (
            <dl className="grid grid-cols-[1fr_2fr] gap-x-4 gap-y-2 bg-plate p-4 text-[13px]">
              <dt className="text-muted-foreground">{uiText("Model")}</dt>
              <dd className="break-words">{modelName}</dd>
              <dt className="text-muted-foreground">{uiText("Current recommended Revision")}</dt>
              <dd>
                {currentRecommendedRevision != null
                  ? `${uiText("Rev ")}${currentRecommendedRevision}`
                  : uiText("None")}
              </dd>
            </dl>
          )}

          <input
            ref={fileInputRef}
            type="file"
            accept={GCODE_ACCEPT}
            onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
            className="sr-only"
          />

          <div className="space-y-1.5">
            <span className="text-sm font-medium text-foreground">{uiText("G-code file")}</span>
            {file ? (
              <div
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
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
                    {uiText("{value1} · G-code", { value1: String(formatBytes(file.size) ?? "") })}
                  </p>
                </div>
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={() => fileInputRef.current?.click()}
                >
                  {uiText("Replace")}
                </Button>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  onClick={() => {
                    selectFile(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                  aria-label={uiText("Remove selected file")}
                >
                  <X className="h-4 w-4" />
                </Button>
              </div>
            ) : (
              <div
                onDragEnter={(event) => {
                  event.preventDefault();
                  setDragActive(true);
                }}
                onDragOver={(event) => event.preventDefault()}
                onDragLeave={() => setDragActive(false)}
                onDrop={onDrop}
                className={cn(
                  "flex items-start gap-3 border border-dashed border-input bg-plate p-3",
                  dragActive && "border-primary bg-accent",
                )}
              >
                <Upload className="mt-2 h-5 w-5 shrink-0 text-muted-foreground" />
                <div>
                  <div className="flex flex-wrap items-center gap-3">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => fileInputRef.current?.click()}
                    >
                      {uiText("Choose file")}
                    </Button>
                    <span className="text-xs text-muted-foreground">
                      {uiText("or drop files here")}
                    </span>
                  </div>
                  <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
                    {uiText(
                      "Select a sliced file. PrintStash stores it as a Revision; it does not slice or validate G-code.",
                    )}
                  </p>
                </div>
              </div>
            )}
            {!file && (
              <p className="pt-1 text-xs text-muted-foreground">{uiText("No files selected")}</p>
            )}
          </div>

          {error && (
            <div
              role="alert"
              className="rounded-md border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
            >
              {error}
            </div>
          )}

          <div className="grid gap-4">
            <label className="space-y-1.5 text-sm font-medium text-foreground">
              {uiText("Revision label ")}
              <span className="font-normal text-muted-foreground">{uiText("Optional")}</span>
              <Input
                value={label}
                onChange={(event) => setLabel(event.target.value)}
                maxLength={128}
                placeholder={uiText("e.g. Stronger walls")}
              />
            </label>
            <label className="space-y-1.5 text-sm font-medium text-foreground">
              {uiText("Notes ")}
              <span className="font-normal text-muted-foreground">{uiText("Optional")}</span>
              <textarea
                value={notes}
                onChange={(event) => setNotes(event.target.value)}
                rows={3}
                placeholder={uiText("What changed in this slice?")}
                className={cn(inputClasses, "h-auto resize-none")}
              />
            </label>
          </div>

          <ChoiceField<"keep" | "replace">
            label={uiText("Recommendation")}
            value={recommended ? "replace" : "keep"}
            onChange={(value) => setRecommended(value === "replace")}
            inline
            disabled={submitting}
            options={[
              {
                value: "keep",
                label:
                  currentRecommendedRevision != null
                    ? uiText("Keep Rev {value1} recommended", {
                        value1: currentRecommendedRevision,
                      })
                    : uiText("Keep current recommendation"),
              },
              { value: "replace", label: uiText("Make this Revision recommended") },
            ]}
          />
          <p className="text-xs text-muted-foreground">
            {uiText("Making this Revision recommended is an explicit choice.")}
          </p>
          <div className="flex items-center justify-between gap-4 border-t border-border pt-4 text-[13px]">
            <span>{uiText("New Revision status")}</span>
            <span className="text-xs text-warning">{uiText("Needs test")}</span>
          </div>
        </form>
      </Modal>
    </Localized>
  );
}
