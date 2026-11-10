"use client";

import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, Loader2 } from "lucide-react";

import { createModelShare, listModelShares, revokeShare } from "@/lib/api/share";
import { toast } from "@/lib/toast";
import { FileRead, ShareLinkRead } from "@/types";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { ChoiceField } from "./choice-field";
import { Localized } from "@/components/ui/localized";

function shareUrl(path: string): string {
  // Server-side rendering has no origin to prefix; the relative path is still usable.
  if (!("window" in globalThis)) return path;
  return `${window.location.origin}${path}`;
}

export function ShareDialog({
  modelId,
  modelName,
  files,
  open,
  onClose,
}: {
  modelId: number;
  modelName?: string;
  files: FileRead[];
  open: boolean;
  onClose: () => void;
}) {
  useUiLocale();
  const [links, setLinks] = useState<ShareLinkRead[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [expiresInDays, setExpiresInDays] = useState(7);
  const [allowDownload, setAllowDownload] = useState(false);
  const gcodeFiles = useMemo(() => files.filter((f) => f.file_type === "gcode"), [files]);
  const initialRevision = gcodeFiles.find((file) => file.is_recommended) ?? gcodeFiles.at(-1);
  const [revisionScope, setRevisionScope] = useState<"all" | "selected">(
    initialRevision ? "selected" : "all",
  );
  const [selectedRevisionIds, setSelectedRevisionIds] = useState<number[]>(
    initialRevision ? [initialRevision.id] : [],
  );
  const [lastToken, setLastToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  // Each open starts from a clean form. Adjusting state during the render that
  // flips `open` (React's documented alternative to a reset effect) keeps the
  // dialog from painting the previous session's selection for one frame.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      const recommended =
        gcodeFiles.find((f) => f.is_recommended) ?? gcodeFiles[gcodeFiles.length - 1];
      setRevisionScope(recommended ? "selected" : "all");
      setSelectedRevisionIds(recommended ? [recommended.id] : []);
      setLoading(true);
    }
  }

  useEffect(() => {
    if (!open) return;
    listModelShares(modelId)
      .then(setLinks)
      .catch((e) => toast.error(e))
      .finally(() => setLoading(false));
  }, [open, modelId]);

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
      toast.success(uiText("Share link created and copied."));
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
      current.includes(id) ? current.filter((candidate) => candidate !== id) : [...current, id],
    );
  }

  const createDisabled =
    creating || (revisionScope === "selected" && selectedRevisionIds.length === 0);

  return (
    <Localized>
      <Modal
        open={open}
        onClose={onClose}
        title={modelName ? uiText("Share {value1}", { value1: modelName }) : uiText("Share model")}
        description={uiText(
          "Choose which Revisions the link exposes, whether files can be downloaded, and when access expires.",
        )}
        className="max-w-160"
        footer={
          <>
            <Button variant="outline" onClick={onClose}>
              {uiText("Close")}
            </Button>
            <Button type="button" onClick={doCreate} disabled={createDisabled} loading={creating}>
              {uiText("Create link")}
            </Button>
          </>
        }
      >
        {modelName && (
          <div className="mb-5 grid grid-cols-[1fr_2fr] gap-4 bg-plate p-4 text-[13px]">
            <span className="text-muted-foreground">{uiText("Model")}</span>
            <span className="break-words">{modelName}</span>
          </div>
        )}
        <p className="mb-4 text-[13px] text-muted-foreground">
          {uiText("Copy the new link after creating it. You can revoke access at any time.")}
        </p>
        <div className="space-y-5">
          {/* Create */}
          <div className="space-y-4">
            <h3 className="text-sm font-semibold">{uiText("Link access")}</h3>
            <div className="space-y-4">
              <label className="block">
                <span className="mb-2 block text-sm text-foreground">
                  {uiText("Expires (days)")}
                </span>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={(e) => setExpiresInDays(Number(e.target.value))}
                  className="w-40 h-9 bg-surface-container-lowest text-on-surface font-mono text-sm border border-outline-variant rounded px-3"
                />
              </label>
              <ChoiceField<"off" | "on">
                label={uiText("Allow file download")}
                value={allowDownload ? "on" : "off"}
                onChange={(value) => setAllowDownload(value === "on")}
                inline
                options={[
                  { value: "off", label: uiText("Off") },
                  { value: "on", label: uiText("On") },
                ]}
              />
            </div>
            {gcodeFiles.length > 0 && (
              <div className="space-y-4 border-t border-border pt-5">
                <h3 className="text-sm font-semibold">{uiText("Included Revisions")}</h3>
                <ChoiceField<"all" | "selected" | number>
                  label={uiText("Revisions")}
                  value={
                    revisionScope === "selected" && selectedRevisionIds.length === 1
                      ? selectedRevisionIds[0]
                      : revisionScope
                  }
                  onChange={(value) => {
                    if (value === "all" || value === "selected") {
                      setRevisionScope(value);
                    } else {
                      setRevisionScope("selected");
                      setSelectedRevisionIds([value]);
                    }
                  }}
                  options={[
                    { value: "all", label: uiText("Every revision") },
                    { value: "selected", label: uiText("Selected revisions") },
                    ...gcodeFiles.map((file) => ({
                      value: file.id,
                      label: `${uiText("Rev ")}${file.gcode_revision_number ?? file.version}`,
                    })),
                  ]}
                  inline
                />
                {revisionScope === "selected" && (
                  <div className="max-h-72 overflow-y-auto divide-y divide-border">
                    {gcodeFiles.map((f) => (
                      <label
                        key={f.id}
                        className="flex items-start gap-3 py-4 hover:bg-surface-container-low"
                      >
                        <input
                          type="checkbox"
                          checked={selectedRevisionIds.includes(f.id)}
                          onChange={() => toggleRevision(f.id)}
                          className="mt-0.5 h-5 w-5 shrink-0 accent-primary"
                        />
                        <span className="min-w-0">
                          <span className="block text-sm font-semibold text-on-surface">
                            {uiText("Rev ")}
                            {f.gcode_revision_number ?? f.version}
                            {f.revision_label ? ` · ${f.revision_label}` : ""}
                            {f.is_recommended ? uiText(" · Recommended") : ""}
                          </span>
                          <span className="mt-1 block break-all text-[13px] text-on-surface-variant">
                            {f.original_filename}
                          </span>
                        </span>
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {lastToken && (
            <div className="rounded border border-primary/40 bg-primary/5 p-3">
              <p className="font-mono text-3xs uppercase tracking-wider text-on-surface-variant mb-1">
                {uiText("New link (copy it now)")}
              </p>
              <div className="flex items-center gap-2">
                <input
                  readOnly
                  value={lastToken}
                  className="flex-1 h-8 bg-surface-container-lowest text-on-surface font-mono text-2xs border border-outline-variant rounded px-2"
                />
                <button
                  aria-label={uiText("Copy link")}
                  onClick={copyLast}
                  className="h-8 w-8 rounded border border-outline-variant flex items-center justify-center hover:bg-surface-container-low"
                >
                  {copied ? (
                    <Check className="h-4 w-4 text-emerald-600" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
          )}

          {/* Existing */}
          <div>
            <h4 className="mb-2 text-sm font-semibold text-foreground">{uiText("Active links")}</h4>
            {loading ? (
              <Loader2 className="h-4 w-4 animate-spin text-on-surface-variant" />
            ) : links.length === 0 ? (
              <p className="text-[13px] text-muted-foreground">{uiText("No share links yet.")}</p>
            ) : (
              <div className="divide-y divide-border border-y border-border">
                {links.map((l) => (
                  <div key={l.id} className="flex items-center justify-between gap-4 py-4">
                    <div className="min-w-0">
                      <p className="text-sm font-medium text-on-surface">
                        {l.is_active
                          ? uiText("Active")
                          : l.revoked_at
                            ? uiText("Revoked")
                            : uiText("Expired")}
                        {l.allow_download ? uiText(" · downloadable") : uiText(" · view-only")}
                        {l.revision_file_ids?.length
                          ? uiText(" · {value1} revs", {
                              value1: String(l.revision_file_ids.length),
                            })
                          : uiText(" · all revs")}
                      </p>
                      <p className="mt-1 text-xs text-on-surface-variant">
                        {uiText("expires {value1} · {value2} views", {
                          value1: String(
                            new Date(l.expires_at).toLocaleDateString(currentLocale()) ?? "",
                          ),
                          value2: String(l.access_count ?? ""),
                        })}
                      </p>
                    </div>
                    {l.is_active && (
                      <button
                        onClick={() => doRevoke(l.id)}
                        className="shrink-0 border border-border px-3 py-2 text-[13px] text-foreground hover:bg-accent"
                      >
                        {uiText("Revoke")}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </Modal>
    </Localized>
  );
}
