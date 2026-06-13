"use client";

import { Download, FileText } from "lucide-react";

import { downloadAuthenticatedFile } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead } from "@/types";

import { SlicerOpenButton } from "@/components/slicer-open-button";

const SLICEABLE_TYPES = new Set(["stl", "3mf", "obj"]);

export function FilesTab({ sourceFiles }: { sourceFiles: FileRead[] }) {
  return (
    <section>
      <h2 className="text-lg font-semibold text-[var(--on-surface)] mb-4 pb-1 border-b border-[var(--outline-variant)]">
        Source Files
      </h2>
      {sourceFiles.length === 0 && (
        <p className="font-mono text-xs text-[var(--on-surface-variant)]">
          No source files (STL / 3MF / OBJ) for this model.
        </p>
      )}
      <div className="space-y-2">
        {sourceFiles.map((f) => (
          <div key={f.id} className="flex items-center justify-between p-3 border border-[var(--outline-variant)] rounded hover:border-[var(--primary)] hover:shadow-sm transition-all group bg-[var(--surface)]">
            <div className="flex items-center gap-3 min-w-0">
              <FileText className="h-5 w-5 flex-shrink-0 text-[var(--outline)] group-hover:text-[var(--primary)]" />
              <div className="min-w-0">
                <p className="text-sm text-[var(--on-surface)] font-medium truncate">
                  {f.original_filename}
                </p>
                <p className="font-mono text-[11px] text-[var(--on-surface-variant)]">
                  {formatBytes(f.size_bytes)} · v{f.version} · Source
                </p>
              </div>
            </div>
            <div className="flex items-center gap-0.5 flex-shrink-0">
              {SLICEABLE_TYPES.has(f.file_type) && (
                <SlicerOpenButton fileId={f.id} />
              )}
              <button
                type="button"
                onClick={() =>
                  downloadAuthenticatedFile(
                    `/api/v1/files/${f.id}/download`,
                    f.original_filename,
                  ).catch((e) => toast.error(e))
                }
                title="Download"
                className="text-[var(--on-surface-variant)] hover:text-[var(--primary)] p-2 rounded hover:bg-[var(--surface-container-high)] transition-colors"
              >
                <Download className="h-5 w-5" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}
