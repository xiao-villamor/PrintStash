"use client";

import { Download, FileText, FolderSync } from "lucide-react";

import { downloadAuthenticatedFile } from "@/lib/api";
import { formatBytes } from "@/lib/format";
import { toast } from "@/lib/toast";
import { FileRead } from "@/types";

import { SlicerOpenButton } from "@/components/slicer-open-button";

const SLICEABLE_TYPES = new Set(["stl", "3mf", "obj"]);

export function FilesTab({ sourceFiles }: { sourceFiles: FileRead[] }) {
  return (
    <section>
      <h2 className="text-lg font-semibold text-on-surface mb-4 pb-1 border-b border-outline-variant">
        Source Files
      </h2>
      {sourceFiles.length === 0 && (
        <p className="font-mono text-xs text-on-surface-variant">
          No source files (STL / 3MF / OBJ) for this model.
        </p>
      )}
      <div className="space-y-2">
        {sourceFiles.map((f) => (
          <div key={f.id} className="flex items-center justify-between p-3 border border-outline-variant rounded hover:border-primary hover:shadow-sm transition-[border-color,box-shadow] group bg-surface">
            <div className="flex items-center gap-3 min-w-0">
              <FileText className="h-5 w-5 flex-shrink-0 text-outline group-hover:text-primary" />
              <div className="min-w-0">
                <p className="flex items-center gap-2 text-sm text-on-surface font-medium truncate">
                  <span className="truncate">{f.original_filename}</span>
                  {f.is_external && (
                    <span
                      title="Stored in a linked shared volume; synced both ways"
                      className="inline-flex items-center gap-1 rounded-full border border-primary/40 bg-primary/10 px-1.5 py-0.5 font-mono text-3xs uppercase tracking-wider text-primary"
                    >
                      <FolderSync className="h-3 w-3" />
                      Linked
                    </span>
                  )}
                </p>
                <p className="font-mono text-2xs text-on-surface-variant">
                  {formatBytes(f.size_bytes)} · v{f.version} · Source
                </p>
              </div>
            </div>
            <div className="flex items-center gap-0.5 flex-shrink-0">
              {SLICEABLE_TYPES.has(f.file_type) && (
                <SlicerOpenButton fileId={f.id} fileType={f.file_type} />
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
                className="text-on-surface-variant hover:text-primary p-2 rounded hover:bg-surface-container-high transition-colors"
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
