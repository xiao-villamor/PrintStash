"use client";

import { useEffect, useState } from "react";
import { Link } from "@/lib/navigation";
import { PrinterRead } from "@/types";
import { createPrinter, deletePrinter, invalidateApiCache, listPrinters } from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Skeleton } from "@/components/ui/skeleton";
import { Plus, Trash2, RefreshCw, ArrowRight, Wifi, WifiOff } from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
  ready: "bg-emerald-500",
  printing: "bg-blue-600 dark:bg-orange-600",
  paused: "bg-amber-500",
  offline: "bg-slate-400",
  unknown: "bg-slate-400",
  error: "bg-red-600",
};

function providerLabel(p: PrinterRead): string {
  return p.provider === "bambu_lan" ? "Bambu LAN" : "Moonraker";
}

function providerAddress(p: PrinterRead): string {
  return p.provider === "bambu_lan"
    ? p.bambu_host || "Bambu LAN"
    : p.moonraker_url;
}

export function PrintersPage({
  initialPrinters,
}: {
  initialPrinters?: PrinterRead[];
}) {
  const auth = useRequireAuth();
  const [printers, setPrinters] = useState<PrinterRead[]>(
    initialPrinters ?? [],
  );
  const [loading, setLoading] = useState(!initialPrinters);
  const [error, setError] = useState<string | null>(null);
  const [addOpen, setAddOpen] = useState(false);

  async function refresh() {
    setLoading(true);
    try {
      invalidateApiCache();
      setPrinters(await listPrinters());
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!initialPrinters) refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleDelete(p: PrinterRead, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm(`Remove printer "${p.name}"?`)) return;
    try {
      await deletePrinter(p.id);
      toast.success(`Printer "${p.name}" removed`);
      await refresh();
    } catch (e) {
      toast.error(e);
    }
  }

  return (
    <div className="flex w-full flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-foreground tracking-tight">Printers</h2>
          <p className="text-sm text-muted-foreground">Connected printer endpoints</p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={refresh}
            className="px-3 py-2 rounded border border-border bg-background text-xs font-medium text-foreground hover:bg-muted transition-colors flex items-center gap-1.5"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Refresh
          </button>
          <button
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setAddOpen(true);
            }}
            disabled={!auth.isAuthenticated}
            className="px-3 py-2 rounded bg-blue-600 dark:bg-orange-600 text-white text-xs font-medium hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors flex items-center gap-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <Plus className="h-3.5 w-3.5" />
            {auth.isAuthenticated ? "Add printer" : "Sign in to add"}
          </button>
        </div>
      </div>

      {error && (
        <div className="rounded border border-red-300/50 bg-red-50/30 p-3 text-sm text-red-600">
          {error}
        </div>
      )}

      {loading ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div
              key={i}
              className="bg-card border border-border rounded p-5 space-y-3"
            >
              <Skeleton className="h-5 w-32" />
              <Skeleton className="h-4 w-48" />
              <Skeleton className="h-4 w-24" />
            </div>
          ))}
        </div>
      ) : printers.length === 0 ? (
        <div className="bg-card border border-border rounded flex flex-col items-center gap-4 py-16 text-muted-foreground">
          <PrinterIcon className="h-12 w-12 opacity-30" />
          <p className="text-sm">No printers configured yet.</p>
          <button
            onClick={() => setAddOpen(true)}
            className="px-4 py-2 rounded bg-blue-600 dark:bg-orange-600 text-white text-xs font-medium hover:bg-blue-700 dark:hover:bg-orange-700 transition-colors flex items-center gap-2"
          >
            <Plus className="h-3.5 w-3.5" />
            Add your first printer
          </button>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {printers.map((p) => (
            <Link
              key={p.id}
              href={`/printers/${p.id}`}
              className="bg-card border border-border rounded hover:shadow-[0_4px_12px_rgba(0,0,0,0.05)] hover:border-blue-600 dark:border-orange-500 transition-all duration-200 p-5 flex flex-col gap-3 group"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="text-[15px] font-semibold text-foreground truncate">
                    {p.name}
                  </h3>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    <span className="rounded border border-border px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-muted-foreground">
                      {providerLabel(p)}
                    </span>
                    {p.capabilities.support_level === "beta" && (
                      <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-amber-600">
                        Beta
                      </span>
                    )}
                  </div>
                </div>
                <span className="flex items-center gap-1.5 flex-shrink-0">
                  <span
                    className={`w-2 h-2 rounded-full ${STATUS_COLORS[p.status] || "bg-slate-400"}`}
                  />
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider">
                    {p.status}
                  </span>
                </span>
              </div>

              <p className="text-[13px] text-muted-foreground truncate">
                {providerAddress(p)}
              </p>

              {p.last_error && (
                <div className="rounded bg-red-50/30 border border-red-300/20 p-2 text-xs text-red-600 truncate">
                  {p.last_error}
                </div>
              )}

              <div className="text-[11px] text-muted-foreground mt-auto">
                {p.last_seen_at
                  ? `Last seen ${new Date(p.last_seen_at).toLocaleString()}`
                  : "Never connected"}
              </div>

              <div className="flex items-center justify-between pt-2 border-t border-border">
                <button
                  onClick={(e) => {
                    if (!auth.isAuthenticated) { e.preventDefault(); e.stopPropagation(); auth.showAuthRequiredToast(); return; }
                    handleDelete(p, e);
                  }}
                  disabled={!auth.isAuthenticated}
                  className="px-2 py-1 rounded text-red-600 hover:bg-red-500/10 transition-colors text-[10px] uppercase tracking-wider flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Trash2 className="h-3 w-3" />
                  {auth.isAuthenticated ? "Remove" : "Sign in"}
                </button>
                <span className="px-2 py-1 rounded border border-border text-foreground text-[10px] uppercase tracking-wider flex items-center gap-1 group-hover:border-blue-600 dark:border-orange-500 group-hover:text-blue-600 dark:text-orange-500 transition-colors">
                  Open
                  <ArrowRight className="h-3 w-3" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}

      {addOpen && (
        <AddPrinterModal
          onClose={() => setAddOpen(false)}
          onCreated={() => {
            setAddOpen(false);
            refresh();
          }}
        />
      )}
    </div>
  );
}

function PrinterIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="6 9 6 2 18 2 18 9" />
      <path d="M6 12H4a2 2 0 0 0-2 2v4a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-4a2 2 0 0 0-2-2h-2" />
      <rect x="6" y="14" width="12" height="8" />
    </svg>
  );
}

function AddPrinterModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [moonrakerKey, setMoonrakerKey] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setErr(null);
    try {
      await createPrinter(
        {
          name: name.trim(),
          moonraker_url: url.trim(),
          api_key: moonrakerKey || undefined,
          notes: notes || undefined,
        },
      );
      toast.success(`Printer "${name.trim()}" added`);
      onCreated();
    } catch (e: any) {
      setErr(e.message);
      toast.error(e);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div
        className="absolute inset-0 bg-black/30 backdrop-blur-sm"
        onClick={onClose}
      />
      <div className="relative bg-card border border-border rounded w-full max-w-md p-6 shadow-lg">
        <h3 className="text-lg font-semibold text-foreground mb-5">
          Add printer
        </h3>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Name
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent"
              placeholder="Voron 2.4"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Moonraker URL
            </label>
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent"
              placeholder="http://voron.local:7125"
              required
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Moonraker API key{" "}
              <span className="font-normal normal-case tracking-normal opacity-60">
                (optional)
              </span>
            </label>
            <input
              type="password"
              value={moonrakerKey}
              onChange={(e) => setMoonrakerKey(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent"
              placeholder="Leave blank if auth is disabled"
            />
          </div>
          <div>
            <label className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Notes
            </label>
            <input
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-blue-600 dark:focus:ring-orange-500 focus:border-transparent"
              placeholder="Optional"
            />
          </div>
          {err && (
            <div className="rounded border border-red-300/40 bg-red-50/30 p-2 text-xs text-red-600">
              {err}
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded border border-border text-muted-foreground text-xs uppercase tracking-wider hover:bg-muted transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || !name || !url}
              className="px-4 py-2 rounded bg-blue-600 dark:bg-orange-600 text-white text-xs uppercase tracking-wider hover:opacity-90 transition-opacity disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? "Adding..." : "Add printer"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
