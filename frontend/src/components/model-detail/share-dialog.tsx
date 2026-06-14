"use client";

import { useEffect, useState } from "react";
import { Check, Copy, Link2, Loader2, X } from "lucide-react";

import {
  createModelShare,
  listModelShares,
  revokeShare,
} from "@/lib/api/share";
import { toast } from "@/lib/toast";
import { ShareLinkRead } from "@/types";

function shareUrl(path: string): string {
  if (typeof window === "undefined") return path;
  return `${window.location.origin}${path}`;
}

export function ShareDialog({
  modelId,
  open,
  onClose,
}: {
  modelId: number;
  open: boolean;
  onClose: () => void;
}) {
  const [links, setLinks] = useState<ShareLinkRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [allowDownload, setAllowDownload] = useState(false);
  const [lastToken, setLastToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    listModelShares(modelId)
      .then(setLinks)
      .catch((e) => toast.error(e))
      .finally(() => setLoading(false));
  }, [open, modelId]);

  if (!open) return null;

  async function doCreate() {
    setCreating(true);
    try {
      const created = await createModelShare(modelId, {
        expires_in_days: expiresInDays,
        allow_download: allowDownload,
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} aria-hidden />
      <div
        className="relative bg-[var(--surface-container-lowest)] border border-[var(--outline-variant)] rounded-md w-full max-w-lg max-h-[90vh] overflow-y-auto shadow-2xl"
        role="dialog"
        aria-modal="true"
      >
        <div className="flex items-start justify-between gap-4 px-6 py-4 border-b border-[var(--outline-variant)]">
          <div>
            <h3 className="text-sm font-semibold text-[var(--on-surface)] flex items-center gap-2">
              <Link2 className="h-4 w-4" /> Share model
            </h3>
            <p className="text-xs text-[var(--on-surface-variant)] mt-0.5">
              Public, expiring, read-only links. Anyone with the link can view this model only.
            </p>
          </div>
          <button onClick={onClose} aria-label="Close" className="h-7 w-7 -mt-1 rounded hover:bg-[var(--surface-container)] flex items-center justify-center text-[var(--on-surface-variant)]">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          {/* Create */}
          <div className="space-y-3">
            <div className="flex items-end gap-3">
              <label className="block">
                <span className="block font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">
                  Expires (days)
                </span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={(e) => setExpiresInDays(Number(e.target.value))}
                  className="w-28 h-9 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-sm border border-[var(--outline-variant)] rounded px-3"
                />
              </label>
              <label className="flex items-center gap-2 h-9">
                <input
                  type="checkbox"
                  checked={allowDownload}
                  onChange={(e) => setAllowDownload(e.target.checked)}
                  className="accent-[var(--primary)]"
                />
                <span className="text-xs text-[var(--on-surface)]">Allow file download</span>
              </label>
            </div>
            <button
              type="button"
              onClick={doCreate}
              disabled={creating}
              className="px-4 py-2 rounded bg-[var(--primary)] text-[var(--primary-foreground)] font-mono text-xs uppercase tracking-wider hover:opacity-90 disabled:opacity-50 flex items-center gap-2"
            >
              {creating ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              Create link
            </button>
          </div>

          {lastToken && (
            <div className="rounded border border-[var(--primary)]/40 bg-[var(--primary)]/5 p-3">
              <p className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-1">
                New link (copy it now)
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={lastToken}
                  className="flex-1 h-8 bg-[var(--surface-container-lowest)] text-[var(--on-surface)] font-mono text-[11px] border border-[var(--outline-variant)] rounded px-2"
                />
                <button onClick={copyLast} className="h-8 w-8 rounded border border-[var(--outline-variant)] flex items-center justify-center hover:bg-[var(--surface-container-low)]">
                  {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
                </button>
              </div>
            </div>
          )}

          {/* Existing */}
          <div>
            <h4 className="font-mono text-[10px] uppercase tracking-wider text-[var(--on-surface-variant)] mb-2">
              Active links
            </h4>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin text-[var(--on-surface-variant)]" />
            ) : links.length === 0 ? (
              <p className="font-mono text-[11px] text-[var(--on-surface-variant)]/70">No share links yet.</p>
            ) : (
              <div className="space-y-2">
                {links.map((l) => (
                  <div key={l.id} className="flex items-center justify-between gap-2 rounded border border-[var(--outline-variant)] px-3 py-2">
                    <div className="min-w-0">
                      <p className="font-mono text-[11px] text-[var(--on-surface)]">
                        {l.is_active ? "Active" : l.revoked_at ? "Revoked" : "Expired"}
                        {l.allow_download ? " · downloadable" : " · view-only"}
                      </p>
                      <p className="font-mono text-[10px] text-[var(--on-surface-variant)]">
                        expires {new Date(l.expires_at).toLocaleDateString()} · {l.access_count} views
                      </p>
                    </div>
                    {l.is_active && (
                      <button
                        onClick={() => doRevoke(l.id)}
                        className="font-mono text-[10px] uppercase tracking-wider text-[var(--error)] hover:underline shrink-0"
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
      </div>
    </div>
  );
}
