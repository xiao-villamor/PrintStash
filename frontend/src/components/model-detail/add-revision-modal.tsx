"use client";

import { useState } from "react";
import { Loader2, Plus, X } from "lucide-react";

import { addGcodeRevision } from "@/lib/api";
import { createTask, updateTask } from "@/lib/task-center";
import { toast } from "@/lib/toast";
import { ModelRead } from "@/types";

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
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
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
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/30 backdrop-blur-sm" onClick={onClose} />
      <form
        onSubmit={submit}
        className="relative w-full max-w-md rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] p-5 shadow-lg space-y-4"
      >
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-lg font-semibold text-[var(--on-surface)]">
            Add G-code revision
          </h3>
          <button type="button" onClick={onClose} className="rounded p-1 text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)]">
            <X className="h-5 w-5" />
          </button>
        </div>
        {error && (
          <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-xs text-[var(--error)]">
            {error}
          </div>
        )}
        <input
          type="file"
          accept=".gcode,.g,.gco"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)]"
        />
        <input
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          maxLength={128}
          placeholder="Revision label"
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)] focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
        />
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
          placeholder="What changed in this slice?"
          className="w-full rounded border border-[var(--outline-variant)] bg-[var(--surface)] px-3 py-2 font-mono text-xs text-[var(--on-surface)] resize-none focus:outline-none focus:ring-2 focus:ring-[var(--primary)]"
        />
        <label className="flex items-center gap-2 font-mono text-xs text-[var(--on-surface-variant)]">
          <input
            type="checkbox"
            checked={recommended}
            onChange={(e) => setRecommended(e.target.checked)}
            className="rounded"
          />
          Mark as recommended
        </label>
        <div className="flex gap-2">
          <button type="button" onClick={onClose} disabled={submitting} className="flex-1 rounded border border-[var(--outline-variant)] py-2 font-mono text-xs uppercase tracking-wider text-[var(--on-surface-variant)] hover:bg-[var(--surface-container-low)] disabled:opacity-50">
            Cancel
          </button>
          <button type="submit" disabled={!file || submitting} className="flex-1 rounded bg-[var(--primary)] py-2 font-mono text-xs uppercase tracking-wider text-[var(--primary-foreground)] hover:opacity-90 disabled:opacity-50 flex items-center justify-center gap-1.5">
            {submitting ? <Loader2 className="h-4 w-4 animate-spin" /> : <Plus className="h-4 w-4" />}
            {submitting ? "Adding..." : "Add revision"}
          </button>
        </div>
      </form>
    </div>
  );
}
