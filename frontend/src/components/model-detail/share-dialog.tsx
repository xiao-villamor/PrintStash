"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Link2, Loader2, X } from "lucide-react";

import {
  createModelShare,
  listModelShares,
  revokeShare,
} from "@/lib/api/share";
import { toast } from "@/lib/toast";
import { FileRead, ShareLinkRead } from "@/types";
import { ModalShell } from "@/components/ui/modal";

function shareUrl(path: string): string {
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path}`;
}

export function ShareDialog({
  modelId,
  files,
  open,
  onClose,
}: {
  modelId: number;
  files: FileRead[];
  open: boolean;
  onClose: () => void;
}) {
  const [links, setLinks] = useState<ShareLinkRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [allowDownload, setAllowDownload] = useState(false);
  const [revisionScope, setRevisionScope] = useState<"all" | "selected">("all");
  const [selectedRevisionIds, setSelectedRevisionIds] = useState<number[]>([]);
  const [lastToken, setLastToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const gcodeFiles = useMemo(
    () => files.filter((f) => f.file_type === "gcode"),
    [files],
  );

  useEffect(() => {
    if (!open) return;
    const recommended = gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
    setRevisionScope("all");
    setSelectedRevisionIds(recommended ? [recommended.id] : []);
    setLoading(true);
    listModelShares(modelId)
      .then(setLinks)
      .catch((e) => toast.error(e))
      .finally(() => setLoading(false));
  }, [open, modelId, gcodeFiles]);

  async function doCreate() {
    setCreating(true);
    try {
      const created = await createModelShare(modelId, {
        expires_in_days: expiresInDays,
        allow_download: allowDownload,
        revision_file_ids: revisionScope === "selected" ? selectedRevisionIds : null,
      });
      const full = shareUrl(created.url);
      setLastToken(full);
      await navigator.clipboard?.writeText(full).catch(() => {});
      toast.success("Share link created and copied.");
      setLinks(await listModelShares(modelId));
    } catch (e) {
      toast.error(e);
    } finally {
      setCreating(false);
    }
  }

  async function doRevoke(id: number) {
    try {
      await revokeShare(id);
      setLinks(await listModelShares(modelId));
    } catch (e) {
      toast.error(e);
    }
  }

  async function copyLast() {
    if (!lastToken) return;
    await navigator.clipboard?.writeText(lastToken).catch(() => {});
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }

  function toggleRevision(id: number) {
    setSelectedRevisionIds((current) =>
      current.includes(id)
        ? current.filter((candidate) => candidate !== id)
        : [...current, id],
    );
  }

  const createDisabled =
    creating || (revisionScope === "selected" && selectedRevisionIds.length === 0);

  return (
    <ModalShell
      open={open}
      onClose={onClose}
      className="bg-surface-container-lowest border border-outline-variant rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
    >
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-outline-variant">
          <div>
            <h3 className="text-sm font-semibold text-on-surface flex items-center gap-2">
              <Link2 className="h-4 w-4" /> Share model
            </h3>
            <p className="text-xs text-on-surface-variant mt-0.5">
              Public, expiring, read-only links. Anyone with the link can view this model only.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-7 w-7 -mt-1 rounded hover:bg-surface-container flex items-center justify-center text-on-surface-variant">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Create */}
          <div className="space-y-3">
            <div className="flex items-end gap-3">
              <label className="block">
                <span className="block font-mono text-3xs uppercase tracking-wider text-on-surface-variant mb-1">
                  Expires (days)
                </span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={(e) => setExpiresInDays(Number(e.target.value))}
                  className="w-28 h-9 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3"
                />
              </label>
              <label className="flex items-center gap-2 h-9">
                <input
                  type="checkbox"
                  checked={allowDownload}
                  onChange={(e) => setAllowDownload(e.target.checked)}
                  className="accent-primary"
                />
                <span className="text-xs text-on-surface">Allow file download</span>
              </label>
            </div>
            {gcodeFiles.length > 0 && (
              <div className="space-y-2">
                <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant">
                  Revisions
                </p>
                <div className="grid grid-cols-2 gap-2">
                  <button
                    type="button"
                    onClick={() => setRevisionScope("all")}
                    className={`rounded border px-3 py-2 text-left font-mono text-2xs ${
                      revisionScope === "all"
                        ? "border-primary bg-secondary-container text-on-secondary-container"
                        : "border-outline-variant text-on-surface-variant hover:bg-surface-container-low"
                    }`}
                  >
                    Every revision
                  </button>
                  <button
                    type="button"
                    onClick={() => setRevisionScope("selected")}
                    className={`rounded border px-3 py-2 text-left font-mono text-2xs ${
                      revisionScope === "selected"
                        ? "border-primary bg-secondary-container text-on-secondary-container"
                        : "border-outline-variant text-on-surface-variant hover:bg-surface-container-low"
                    }`}
                  >
                    Selected revisions
                  </button>
                </div>
                {revisionScope === "selected" && (
                  <div className="max-h-44 overflow-y-auto rounded border border-outline-variant divide-y divide-outline-variant">
                    {gcodeFiles.map((f) => (
                      <label key={f.id} className="flex items-start gap-2 px-3 py-2 hover:bg-surface-container-low">
                        <input
                          type="checkbox"
                          checked={selectedRevisionIds.includes(f.id)}
                          onChange={() => toggleRevision(f.id)}
                          className="mt-0.5 accent-primary"
                        />
                        <span className="min-w-0">
                          <span className="block text-xs text-on-surface truncate">
                            Rev {f.gcode_revision_number ?? f.version}
                            {f.revision_label ? ` · ${f.revision_label}` : ""}
                            {f.is_recommended ? " · Recommended" : ""}
                          </span>
                          <span className="block font-mono text-3xs text-on-surface-variant truncate">
                            {f.original_filename}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
            <button
              type="button"
              onClick={doCreate}
              disabled={createDisabled}
              className="px-4 py-2 rounded bg-primary text-primary-foreground font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
            >
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              Create link
            </button>
          </div>

          {lastToken && (
            <div className="rounded border border-primary/40 bg-primary/5 p-3">
              <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant mb-1">
                New link (copy it now)
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={lastToken}
                  className="flex-1 h-8 bg-surface-container-lowest text-on-surface font-mono text-2xs border border-outline-variant rounded px-2"
                />
                <button onClick={copyLast} className="h-8 w-8 rounded border border-outline-variant flex items-center justify-center hover:bg-surface-container-low">
                  {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
                </button>
              </div>
            </div>
          )}

          {/* Existing */}
          <div>
            <h4 className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant mb-2">
              Active links
            </h4>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin text-on-surface-variant" />
            ) : links.length === 0 ? (
              <p className="font-mono text-2xs text-on-surface-variant/70">No share links yet.</p>
            ) : (
              <div className="space-y-2">
                {links.map((l) => (
                  <div key={l.id} className="flex items-center justify-between gap-2 rounded border border-outline-variant px-3 py-2">
                    <div className="min-w-0">
                      <p className="font-mono text-2xs text-on-surface">
                        {l.is_active ? "Active" : l.revoked_at ? "Revoked" : "Expired"}
                        {l.allow_download ? " · downloadable" : " · view-only"}
                        {l.revision_file_ids?.length ? ` · ${l.revision_file_ids.length} revs` : " · all revs"}
                      </p>
                      <p className="font-mono text-3xs text-on-surface-variant">
                        expires {new Date(l.expires_at).toLocaleDateString()} · {l.access_count} views
                      </p>
                    </div>
                    {l.is_active && (
                      <button
                        onClick={() => doRevoke(l.id)}
                        className="font-mono text-3xs uppercase tracking-wider text-error hover:underline shrink-0"
                      >
                        Revoke
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
    </ModalShell>
  );
}
