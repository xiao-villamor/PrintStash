"use client";

import { useRef, useState } from "react";
import { Link } from "@/lib/navigation";
import { PrinterRead } from "@/types";
import { createPrinter, deletePrinter, updatePrinter } from "@/lib/api";
import { usePrinters } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import {
  PRINTER_MODEL_OPTIONS,
  PRINTER_SETUP_OPTIONS,
  providerAddress,
  providerLabel,
  setupProviderFields,
  type PrinterSetupKind,
} from "@/lib/printer-providers";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Modal, ModalShell } from "@/components/ui/modal";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { PageHeader } from "@/components/ui/page-header";
import { readPrinterCardImagePreference } from "@/lib/printer-card-display";
import { printerArtwork } from "@/lib/orca-printer-images";
import { Plus, Trash2, RefreshCw, ArrowRight, Pencil, Printer as PrinterIcon, Network, Clock3, Search, Check } from "lucide-react";

const STATUS_COLORS: Record<string, string> = {
  ready: "bg-emerald-500",
  printing: "bg-primary",
  paused: "bg-amber-500",
  offline: "bg-slate-400",
  unknown: "bg-slate-400",
  error: "bg-red-600",
};

export function PrintersPage() {
  const auth = useRequireAuth();
  // Shared printers cache: mutations through the api layer invalidate
  // queryKeys.printers, so this list refetches itself after add/delete.
  const printersQuery = usePrinters();
  const printers = printersQuery.data ?? [];
  const loading = printersQuery.isLoading;
  const error =
    printersQuery.error instanceof Error ? printersQuery.error.message : null;
  const [addOpen, setAddOpen] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<PrinterRead | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [showCardImages] = useState(readPrinterCardImagePreference);

  function handleDelete(p: PrinterRead, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    setDeleteTarget(p);
  }

  async function confirmDelete() {
    if (!deleteTarget) return;
    const printer = deleteTarget;
    setDeleteBusy(true);
    try {
      await deletePrinter(printer.id);
      toast.success(`Printer "${printer.name}" removed`);
      setDeleteTarget(null);
    } catch (e) {
      toast.error(e);
    } finally {
      setDeleteBusy(false);
    }
  }

  return (
    <>
      <ConfirmModal
        open={!!deleteTarget}
        onClose={() => setDeleteTarget(null)}
        onConfirm={confirmDelete}
        busy={deleteBusy}
        title="Remove printer?"
        description={deleteTarget ? `"${deleteTarget.name}" will be removed from PrintStash.` : "This printer will be removed from PrintStash."}
        confirmLabel="Remove"
      />
      <div className="flex w-full flex-col gap-6">
      <PageHeader
        title="Printers"
        description="Connected printer endpoints"
        actions={
          <>
            <Button variant="outline" size="xs" onClick={() => printersQuery.refetch()}>
              <RefreshCw className="h-3.5 w-3.5" />
              Refresh
            </Button>
            <Button
              size="xs"
              onClick={() => {
                if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                setAddOpen(true);
              }}
              disabled={!auth.isAuthenticated}
            >
              <Plus className="h-3.5 w-3.5" />
              {auth.isAuthenticated ? "Add printer" : "Sign in to add"}
            </Button>
          </>
        }
      />

      {error && (
        <div className="animate-panel-in rounded border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      {loading ? (
        <div className="grid animate-panel-in grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
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
        <EmptyState
          icon={PrinterIcon}
          title="No printers configured yet."
          action={
            <Button size="xs" onClick={() => setAddOpen(true)}>
              <Plus className="h-3.5 w-3.5" />
              Add your first printer
            </Button>
          }
          className="animate-panel-in rounded-lg border border-border bg-card shadow-sm"
        />
      ) : (
        <div className="stagger-children grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {printers.map((p) => (
            <article
              key={p.id}
              className="animate-card-in flex min-h-72 flex-col overflow-hidden rounded-lg border border-border bg-card shadow-sm"
            >
              {showCardImages && (
                <PrinterCardArtwork
                  key={`${p.id}:${p.model_name || p.detected_model || "unknown"}`}
                  printer={p}
                />
              )}
              <div className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
                <div className="min-w-0">
                  <Link href={`/printers/${p.id}`} className="text-[15px] font-semibold text-foreground hover:text-primary">
                    {p.name}
                  </Link>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className="h-5 border-border bg-muted/50 px-2 font-mono text-3xs font-medium uppercase tracking-wider text-muted-foreground"
                    >
                      {providerLabel(p)}
                    </Badge>
                    {p.capabilities.support_level === "beta" && (
                      <span className="rounded border border-amber-500/40 bg-amber-500/10 px-1.5 py-0.5 text-3xs uppercase tracking-wider text-amber-600">
                        Beta
                      </span>
                    )}
                  </div>
                </div>
                <span className="flex items-center gap-1.5 flex-shrink-0">
                  <span
                    className={`w-2 h-2 rounded-full ${STATUS_COLORS[p.status] || "bg-slate-400"}`}
                  />
                  <span className="text-3xs text-muted-foreground uppercase tracking-wider">
                    {p.status}
                  </span>
                </span>
              </div>

              <div className="flex flex-1 flex-col gap-4 px-5 py-4">
                <dl className="grid gap-3 text-sm">
                  <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-3">
                    <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <PrinterIcon className="h-3.5 w-3.5" /> Model
                    </dt>
                    <dd className="min-w-0">
                      <PrinterModelBadge printer={p} canEdit={auth.isAuthenticated} />
                    </dd>
                  </div>
                  <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-3">
                    <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Network className="h-3.5 w-3.5" /> Endpoint
                    </dt>
                    <dd className="truncate font-mono text-xs text-foreground" title={providerAddress(p)}>
                      {providerAddress(p)}
                    </dd>
                  </div>
                  <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] items-center gap-3">
                    <dt className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <Clock3 className="h-3.5 w-3.5" /> Activity
                    </dt>
                    <dd className="text-xs text-foreground">
                      {p.last_seen_at
                        ? `Seen ${new Date(p.last_seen_at).toLocaleString()}`
                        : "Never connected"}
                    </dd>
                  </div>
                </dl>

                {p.notes && (
                  <p className="line-clamp-2 text-xs leading-relaxed text-muted-foreground">
                    {p.notes}
                  </p>
                )}

                {p.last_error && (
                  <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/10 p-2.5 text-xs leading-relaxed text-destructive">
                    <span className="font-medium">Connection issue: </span>
                    <span className="line-clamp-2">{p.last_error}</span>
                  </div>
                )}
              </div>

              <div className="flex items-center justify-between border-t border-border bg-muted/20 px-4 py-3">
                <button
                  onClick={(e) => {
                    if (!auth.isAuthenticated) { e.preventDefault(); e.stopPropagation(); auth.showAuthRequiredToast(); return; }
                    handleDelete(p, e);
                  }}
                  disabled={!auth.isAuthenticated}
                  className="px-2 py-1 rounded text-red-600 hover:bg-red-500/10 transition-colors text-3xs uppercase tracking-wider flex items-center gap-1 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <Trash2 className="h-3 w-3" />
                  {auth.isAuthenticated ? "Remove" : "Sign in"}
                </button>
                <Link href={`/printers/${p.id}`} className="flex items-center gap-1 rounded border border-border px-2.5 py-1.5 text-3xs uppercase tracking-wider text-foreground transition-colors hover:border-primary hover:text-primary">
                  Open
                  <ArrowRight className="h-3 w-3" />
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}

      {addOpen && (
        <AddPrinterModal
          onClose={() => setAddOpen(false)}
          onCreated={() => {
            // createPrinter already invalidated queryKeys.printers; just close.
            setAddOpen(false);
          }}
        />
      )}
      </div>
    </>
  );
}

function PrinterCardArtwork({ printer }: { printer: PrinterRead }) {
  const modelName = printer.model_name || printer.detected_model;
  const artwork = printerArtwork(modelName);
  const [imageUrl, setImageUrl] = useState(artwork.imageUrl);
  const usingFallback = imageUrl === "/images/printers/generic-fdm.png";

  return (
    <div className="relative flex h-44 items-center justify-center border-b border-border bg-muted/40 px-6 py-4">
      <img
        src={imageUrl}
        alt={`${modelName || printer.name} printer`}
        referrerPolicy="no-referrer"
        onError={() => setImageUrl("/images/printers/generic-fdm.png")}
        className="h-full w-full object-contain"
      />
      {!usingFallback && (
        <a
          href={artwork.sourceUrl}
          target="_blank"
          rel="noreferrer noopener"
          className="absolute bottom-2 right-2 rounded bg-background/90 px-1.5 py-0.5 text-3xs text-muted-foreground hover:text-foreground"
        >
          Image: OrcaSlicer
        </a>
      )}
    </div>
  );
}

const OTHER_MODEL_OPTION = "__other__";

function PrinterModelBadge({
  printer,
  canEdit,
}: {
  printer: PrinterRead;
  canEdit: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const displayModel = printer.model_name || printer.detected_model;
  const [selected, setSelected] = useState(() =>
    displayModel && PRINTER_MODEL_OPTIONS.includes(displayModel)
      ? displayModel
      : displayModel
        ? OTHER_MODEL_OPTION
        : "",
  );
  const [customValue, setCustomValue] = useState(
    displayModel && !PRINTER_MODEL_OPTIONS.includes(displayModel) ? displayModel : "",
  );
  const [saving, setSaving] = useState(false);

  async function save() {
    const modelName = selected === OTHER_MODEL_OPTION ? customValue.trim() : selected;
    setSaving(true);
    try {
      await updatePrinter(printer.id, { model_name: modelName });
      setEditing(false);
    } catch (err) {
      toast.error(err);
    } finally {
      setSaving(false);
    }
  }

  if (editing) {
    return (
      <PrinterModelPicker
        selected={selected}
        customValue={customValue}
        saving={saving}
        onSelectedChange={setSelected}
        onCustomValueChange={setCustomValue}
        onClose={() => setEditing(false)}
        onSave={save}
      />
    );
  }

  return (
    <button
      type="button"
      disabled={!canEdit}
      className="flex max-w-full items-center gap-1 truncate rounded-md text-left text-xs font-medium text-foreground enabled:hover:text-primary disabled:cursor-default"
      onClick={(e) => {
        if (!canEdit) return;
        e.preventDefault();
        e.stopPropagation();
        setEditing(true);
      }}
    >
      {displayModel || (canEdit ? "Set model" : "Model unknown")}
      {canEdit && <Pencil className="h-2.5 w-2.5 opacity-60" />}
    </button>
  );
}

const MODEL_BRANDS = ["All", ...Array.from(new Set(PRINTER_MODEL_OPTIONS.map((model) => model.split(" ")[0])))];

function PrinterModelPicker({
  selected,
  customValue,
  saving,
  onSelectedChange,
  onCustomValueChange,
  onClose,
  onSave,
}: {
  selected: string;
  customValue: string;
  saving: boolean;
  onSelectedChange: (model: string) => void;
  onCustomValueChange: (value: string) => void;
  onClose: () => void;
  onSave: () => void;
}) {
  const [query, setQuery] = useState("");
  const [brand, setBrand] = useState("All");
  const normalizedQuery = query.trim().toLocaleLowerCase();
  const models = PRINTER_MODEL_OPTIONS.filter((model) =>
    (brand === "All" || model.startsWith(`${brand} `)) &&
    (!normalizedQuery || model.toLocaleLowerCase().includes(normalizedQuery)),
  );
  const canSave = Boolean(selected && (selected !== OTHER_MODEL_OPTION || customValue.trim()));

  return (
    <Modal open onClose={onClose} title="Select printer model" className="flex max-h-[min(48rem,calc(100vh-2rem))] max-w-5xl flex-col overflow-hidden">
      <div className="border-b border-border pb-4">
        <label className="relative block">
          <span className="sr-only">Search printer models</span>
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search printer models"
            className="w-full rounded-md border border-input bg-background py-2 pl-9 pr-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="mt-3 flex gap-1 overflow-x-auto pb-1" aria-label="Printer brands">
          {MODEL_BRANDS.map((item) => (
            <button
              key={item}
              type="button"
              aria-pressed={brand === item}
              onClick={() => setBrand(item)}
              className={`shrink-0 rounded-md px-3 py-1.5 text-xs font-medium transition-[background-color,color,transform] duration-press ease-out active:scale-[0.98] ${brand === item ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-popover-hover hover:text-popover-foreground"}`}
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto bg-muted/20 p-4">
        {models.length ? (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
            {models.map((model) => {
              const artwork = printerArtwork(model);
              const active = selected === model;
              return (
                <button
                  key={model}
                  type="button"
                  aria-pressed={active}
                  onClick={() => onSelectedChange(model)}
                  className={`group relative flex min-h-44 flex-col rounded-lg border bg-card p-3 text-left shadow-sm outline-none transition-[background-color,border-color,transform] duration-press ease-out hover:border-primary/60 focus-visible:ring-2 focus-visible:ring-ring active:scale-[0.99] ${active ? "border-primary ring-2 ring-primary-soft" : "border-border"}`}
                >
                  {active && <span className="absolute right-2 top-2 z-10 rounded-full bg-primary p-1 text-primary-foreground"><Check className="h-3 w-3" /></span>}
                  <img src={artwork.imageUrl} alt="" className="mb-2 h-28 w-full object-contain" referrerPolicy="no-referrer" />
                  <span className="mt-auto text-xs font-semibold leading-snug text-foreground">{model}</span>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="flex min-h-52 items-center justify-center text-sm text-muted-foreground">No printer models match your search.</div>
        )}
      </div>

      <div className="border-t border-border bg-background pt-4">
        <label className="flex items-center gap-3">
          <input type="radio" checked={selected === OTHER_MODEL_OPTION} onChange={() => onSelectedChange(OTHER_MODEL_OPTION)} className="h-4 w-4 accent-primary" />
          <span className="shrink-0 text-sm font-medium text-foreground">Custom model</span>
          <input
            value={customValue}
            onFocus={() => onSelectedChange(OTHER_MODEL_OPTION)}
            onChange={(event) => { onSelectedChange(OTHER_MODEL_OPTION); onCustomValueChange(event.target.value); }}
            placeholder="Enter model name"
            className="min-w-0 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </label>
        <div className="mt-4 flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
          <Button type="button" loading={saving} disabled={!canSave} onClick={onSave}>Save model</Button>
        </div>
      </div>
    </Modal>
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
  const [setupKind, setSetupKind] = useState<PrinterSetupKind>("moonraker");
  const [url, setUrl] = useState("");
  const [moonrakerKey, setMoonrakerKey] = useState("");
  const [bambuSerial, setBambuSerial] = useState("");
  const [bambuAccessCode, setBambuAccessCode] = useState("");
  const [prusaAuthMode, setPrusaAuthMode] = useState<"digest" | "api_key">("digest");
  const [prusaUsername, setPrusaUsername] = useState("maker");
  const [prusaSecret, setPrusaSecret] = useState("");
  const [octoprintApiKey, setOctoprintApiKey] = useState("");
  const [centauriAccessCode, setCentauriAccessCode] = useState("");
  const [centauriMainboardId, setCentauriMainboardId] = useState("");
  const [notes, setNotes] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submittingRef = useRef(false);
  const [err, setErr] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (submittingRef.current) return;
    submittingRef.current = true;
    setSubmitting(true);
    setErr(null);
    try {
      await createPrinter(
        {
          name: name.trim(),
          ...setupProviderFields(setupKind),
          ...(setupKind === "moonraker" || setupKind === "elegoo_neptune4"
            ? {
                moonraker_url: url.trim(),
                api_key: moonrakerKey || undefined,
              }
            : {}),
          ...(setupKind === "bambu_lan"
            ? {
                bambu_host: url.trim(),
                bambu_serial: bambuSerial.trim(),
                bambu_access_code: bambuAccessCode,
              }
            : {}),
          ...(setupKind === "prusalink"
            ? {
                prusalink_url: url.trim(),
                prusalink_auth_mode: prusaAuthMode,
                prusalink_username:
                  prusaAuthMode === "digest" ? prusaUsername.trim() : undefined,
                prusalink_password:
                  prusaAuthMode === "digest" ? prusaSecret : undefined,
                prusalink_api_key:
                  prusaAuthMode === "api_key" ? prusaSecret : undefined,
              }
            : {}),
          ...(setupKind === "elegoo_centauri_carbon" ||
          setupKind === "elegoo_centauri_carbon_2"
            ? {
                elegoo_centauri_host: url.trim(),
                elegoo_centauri_access_code:
                  setupKind === "elegoo_centauri_carbon_2"
                    ? centauriAccessCode
                    : undefined,
                elegoo_centauri_mainboard_id:
                  setupKind === "elegoo_centauri_carbon"
                    ? centauriMainboardId.trim() || undefined
                    : undefined,
              }
            : {}),
          ...(setupKind === "octoprint"
            ? {
                octoprint_url: url.trim(),
                octoprint_api_key: octoprintApiKey,
              }
            : {}),
          notes: notes || undefined,
        },
      );
      toast.success(`Printer "${name.trim()}" added`);
      onCreated();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "Could not add printer");
      toast.error(e);
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
    }
  }

  return (
    <ModalShell
      onClose={onClose}
      className="bg-card border border-border rounded w-full max-w-md p-6 shadow-lg"
    >
        <h3 className="text-lg font-semibold text-foreground mb-5">
          Add printer
        </h3>
        <form onSubmit={submit} className="space-y-4">
          <div>
            <label htmlFor="printer-name" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Name
            </label>
            <input
              id="printer-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder="Voron 2.4"
              required
            />
          </div>
          <div>
            <label htmlFor="printer-integration" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Integration
            </label>
            <select
              id="printer-integration"
              value={setupKind}
              onChange={(e) => {
                setSetupKind(e.target.value as PrinterSetupKind);
                setUrl("");
              }}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]"
            >
              {PRINTER_SETUP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
            <p className="mt-1.5 text-xs text-muted-foreground">
              {PRINTER_SETUP_OPTIONS.find((option) => option.value === setupKind)?.description}
            </p>
          </div>
          <div>
            <label htmlFor="printer-address" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              {setupKind === "prusalink"
                ? "PrusaLink URL"
                : setupKind === "octoprint"
                  ? "OctoPrint URL"
                  : setupKind === "moonraker"
                    ? "Moonraker URL"
                    : setupKind === "elegoo_neptune4"
                      ? "Printer URL"
                      : "Printer host or IP"}
            </label>
            <input
              id="printer-address"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder={
                setupKind === "prusalink"
                  ? "http://mk4.local"
                  : setupKind === "octoprint"
                    ? "http://octopi.local"
                    : setupKind === "moonraker" || setupKind === "elegoo_neptune4"
                      ? "http://printer.local:7125"
                      : "192.168.1.50"
              }
              required
            />
          </div>
          {(setupKind === "moonraker" || setupKind === "elegoo_neptune4") && <div>
            <label htmlFor="moonraker-api-key" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              {setupKind === "elegoo_neptune4" ? "API key" : "Moonraker API key"}{" "}
              <span className="font-normal normal-case tracking-normal opacity-60">
                (optional)
              </span>
            </label>
            <input
              id="moonraker-api-key"
              type="password"
              value={moonrakerKey}
              onChange={(e) => setMoonrakerKey(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder="Leave blank if auth is disabled"
            />
          </div>}
          {setupKind === "bambu_lan" && <>
            <div>
              <label htmlFor="bambu-serial" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">Printer serial</label>
              <input id="bambu-serial" value={bambuSerial} onChange={(e) => setBambuSerial(e.target.value)} required className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]" />
            </div>
            <div>
              <label htmlFor="bambu-access-code" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">LAN access code</label>
              <input id="bambu-access-code" type="password" value={bambuAccessCode} onChange={(e) => setBambuAccessCode(e.target.value)} required className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]" />
            </div>
          </>}
          {setupKind === "prusalink" && <>
            <div>
              <label htmlFor="prusalink-auth" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">Authentication</label>
              <select id="prusalink-auth" value={prusaAuthMode} onChange={(e) => { setPrusaAuthMode(e.target.value as "digest" | "api_key"); setPrusaSecret(""); }} className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]">
                <option value="digest">Username and password (recommended)</option>
                <option value="api_key">Legacy API key</option>
              </select>
            </div>
            {prusaAuthMode === "digest" && <div>
              <label htmlFor="prusalink-username" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">Username</label>
              <input id="prusalink-username" value={prusaUsername} onChange={(e) => setPrusaUsername(e.target.value)} required className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]" />
            </div>}
            <div>
              <label htmlFor="prusalink-secret" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">{prusaAuthMode === "digest" ? "Password" : "API key"}</label>
              <input id="prusalink-secret" type="password" value={prusaSecret} onChange={(e) => setPrusaSecret(e.target.value)} required className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]" />
            </div>
          </>}
          {setupKind === "octoprint" && <div>
            <label htmlFor="octoprint-api-key" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">API key</label>
            <input id="octoprint-api-key" type="password" value={octoprintApiKey} onChange={(e) => setOctoprintApiKey(e.target.value)} required className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]" />
          </div>}
          {setupKind === "elegoo_centauri_carbon" && <div>
            <label htmlFor="centauri-mainboard-id" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Mainboard ID <span className="font-normal normal-case tracking-normal opacity-60">(recommended)</span>
            </label>
            <input
              id="centauri-mainboard-id"
              value={centauriMainboardId}
              onChange={(e) => setCentauriMainboardId(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]"
              placeholder="From printer discovery or diagnostics"
            />
            <p className="mt-1.5 text-xs text-muted-foreground">Needed for reliable reconnection while paused or errored.</p>
          </div>}
          {setupKind === "elegoo_centauri_carbon_2" && <>
            <div className="rounded border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-muted-foreground">
              Enable LAN Only in printer network settings before connecting.
            </div>
            <div>
              <label htmlFor="centauri-access-code" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">Printer access code</label>
              <input
                id="centauri-access-code"
                type="password"
                value={centauriAccessCode}
                onChange={(e) => setCentauriAccessCode(e.target.value)}
                required
                className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px]"
              />
            </div>
          </>}
          <div>
            <label htmlFor="printer-notes" className="block text-xs text-muted-foreground tracking-wider uppercase mb-1.5">
              Notes
            </label>
            <input
              id="printer-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              className="w-full bg-background text-foreground text-sm border border-border rounded px-3 py-[7px] focus:outline-none focus:ring-2 focus:ring-ring focus:border-transparent"
              placeholder="Optional"
            />
          </div>
          {err && (
            <div className="rounded border border-red-300/40 bg-red-50/30 p-2 text-xs text-red-600">
              {err}
            </div>
          )}
          <div className="flex justify-end gap-3 pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              loading={submitting}
              disabled={!name || !url}
            >
              Add printer
            </Button>
          </div>
        </form>
    </ModalShell>
  );
}
