"use client";

import { useEffect, useState } from "react";
import { FilamentProfileRead, PrinterProfileRead } from "@/types";
import {
  createFilamentProfile,
  createPrinterProfile,
  deleteFilamentProfile,
  deletePrinterProfile,
  listFilamentProfiles,
  listPrinterProfiles,
  syncSpoolmanFilaments,
  updateFilamentProfile,
  updatePrinterProfile,
} from "@/lib/api";
import { useSpoolmanStatus } from "@/lib/queries";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Check, Layers, Loader2, Plus, Printer, RefreshCw, Trash2, X, ChevronDown } from "lucide-react";

type FilamentEdit = {
  name: string;
  materialType: string;
  materialBrand: string;
  cost: string;
  notes: string;
};

type PrinterEdit = {
  name: string;
  model: string;
  nozzle: string;
  notes: string;
};

// Solid-bordered input for the "add preset" panels.
const formInputClass =
  "h-8 w-full rounded border border-border bg-background px-2.5 text-xs text-foreground outline-none transition-shadow placeholder:text-muted-foreground/60 focus:border-transparent focus:ring-2 focus:ring-ring disabled:opacity-40";

// Ghost input for the inline list rows: reads as plain text, reveals an
// editable affordance on hover/focus. Edits auto-save on blur.
const inputClass =
  "h-8 w-full rounded border border-transparent bg-transparent px-2.5 text-xs text-foreground outline-none transition-colors placeholder:text-muted-foreground/40 hover:border-border focus:border-transparent focus:bg-background focus:ring-2 focus:ring-ring disabled:opacity-40 disabled:hover:border-transparent";

function parseOptionalNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

function formatCost(value: number | null): string {
  return value == null ? "" : String(Number(value.toFixed(2)));
}

function formatNozzle(value: number | null): string {
  return value == null ? "" : String(value);
}

function filamentEdit(profile: FilamentProfileRead): FilamentEdit {
  return {
    name: profile.name,
    materialType: profile.material_type ?? "",
    materialBrand: profile.material_brand ?? "",
    cost: formatCost(profile.cost_per_kg),
    notes: profile.notes ?? "",
  };
}

function printerEdit(profile: PrinterProfileRead): PrinterEdit {
  return {
    name: profile.name,
    model: profile.printer_model ?? "",
    nozzle: formatNozzle(profile.nozzle_diameter_mm),
    notes: profile.notes ?? "",
  };
}

function filamentDirty(profile: FilamentProfileRead, edit: FilamentEdit): boolean {
  const base = filamentEdit(profile);
  return (
    base.name !== edit.name ||
    base.materialType !== edit.materialType ||
    base.materialBrand !== edit.materialBrand ||
    base.cost !== edit.cost ||
    base.notes !== edit.notes
  );
}

function printerDirty(profile: PrinterProfileRead, edit: PrinterEdit): boolean {
  const base = printerEdit(profile);
  return (
    base.name !== edit.name ||
    base.model !== edit.model ||
    base.nozzle !== edit.nozzle ||
    base.notes !== edit.notes
  );
}

const MATERIAL_COLORS: Record<string, string> = {
  pla: "bg-emerald-500",
  petg: "bg-primary",
  abs: "bg-primary",
  asa: "bg-teal-500",
  tpu: "bg-purple-500",
  flex: "bg-purple-400",
  nylon: "bg-yellow-500",
  pa: "bg-yellow-500",
  resin: "bg-red-500",
  pc: "bg-sky-500",
  hips: "bg-amber-400",
};

function materialColor(type: string): string {
  const key = type.toLowerCase().trim();
  return MATERIAL_COLORS[key] ?? "bg-slate-400";
}

function ColLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <span className={`text-3xs font-semibold uppercase tracking-wider text-muted-foreground ${className ?? ""}`}>
      {children}
    </span>
  );
}

function RowStatus({ state }: { state?: "saving" | "saved" }) {
  if (state === "saving") return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />;
  if (state === "saved") return <Check className="h-3.5 w-3.5 text-emerald-500" />;
  return null;
}

export function FilamentProfilesCard() {
  const auth = useRequireAuth();
  const [filaments, setFilaments] = useState<FilamentProfileRead[]>([]);
  const [printers, setPrinters] = useState<PrinterProfileRead[]>([]);
  const [filamentEdits, setFilamentEdits] = useState<Record<number, FilamentEdit>>({});
  const [printerEdits, setPrinterEdits] = useState<Record<number, PrinterEdit>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Per-row auto-save indicator, keyed "f{id}" / "p{id}".
  const [rowStatus, setRowStatus] = useState<Record<string, "saving" | "saved">>({});

  // Spoolman sync: when enabled, presets can be imported/refreshed from Spoolman
  // (the source of truth). Synced presets are read-only here.
  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const [syncing, setSyncing] = useState(false);

  // add form state — filament
  const [showAddFilament, setShowAddFilament] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("");
  const [newBrand, setNewBrand] = useState("");
  const [newCost, setNewCost] = useState("");
  const [newNotes, setNewNotes] = useState("");

  // add form state — printer
  const [showAddPrinter, setShowAddPrinter] = useState(false);
  const [newPrinterName, setNewPrinterName] = useState("");
  const [newPrinterModel, setNewPrinterModel] = useState("");
  const [newPrinterNozzle, setNewPrinterNozzle] = useState("");
  const [newPrinterNotes, setNewPrinterNotes] = useState("");

  async function refresh() {
    try {
      const [nextFilaments, nextPrinters] = await Promise.all([
        listFilamentProfiles(),
        listPrinterProfiles(),
      ]);
      setFilaments(nextFilaments);
      setPrinters(nextPrinters);
      setFilamentEdits(
        Object.fromEntries(nextFilaments.map((p) => [p.id, filamentEdit(p)])),
      );
      setPrinterEdits(
        Object.fromEntries(nextPrinters.map((p) => [p.id, printerEdit(p)])),
      );
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { refresh(); }, []);

  function updateFilamentEdit(id: number, patch: Partial<FilamentEdit>) {
    setFilamentEdits((cur) => ({
      ...cur,
      [id]: { ...(cur[id] ?? { name: "", materialType: "", materialBrand: "", cost: "", notes: "" }), ...patch },
    }));
  }

  function updatePrinterEdit(id: number, patch: Partial<PrinterEdit>) {
    setPrinterEdits((cur) => ({
      ...cur,
      [id]: { ...(cur[id] ?? { name: "", model: "", nozzle: "", notes: "" }), ...patch },
    }));
  }

  async function handleCreateFilament() {
    const trimmedName = newName.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const parsedCost = parseOptionalNumber(newCost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) { toast.error("Invalid filament cost"); return; }
    try {
      await createFilamentProfile({
        name: trimmedName,
        material_type: newType.trim() || null,
        material_brand: newBrand.trim() || null,
        cost_per_kg: parsedCost,
        notes: newNotes.trim() || null,
      });
      setNewName(""); setNewType(""); setNewBrand(""); setNewCost(""); setNewNotes("");
      setShowAddFilament(false);
      toast.success(`Filament preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  function flashSaved(key: string) {
    setRowStatus((s) => ({ ...s, [key]: "saved" }));
    setTimeout(() => setRowStatus((s) => { const n = { ...s }; delete n[key]; return n; }), 1500);
  }

  function clearStatus(key: string) {
    setRowStatus((s) => { const n = { ...s }; delete n[key]; return n; });
  }

  // Save when focus leaves the row entirely (not when tabbing between its fields).
  function handleRowBlur(e: React.FocusEvent<HTMLDivElement>, save: () => void) {
    if (!e.currentTarget.contains(e.relatedTarget as Node | null)) save();
  }

  async function handleSyncSpoolman() {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    setSyncing(true);
    try {
      const r = await syncSpoolmanFilaments();
      toast.success(
        `Synced from Spoolman — ${r.created} added, ${r.updated + r.adopted} updated`,
      );
      await refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
    finally { setSyncing(false); }
  }

  async function autoSaveFilament(profile: FilamentProfileRead) {
    if (!auth.isAuthenticated) return;
    // Synced presets mirror Spoolman and are read-only here.
    if (profile.spoolman_filament_id != null) return;
    const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
    if (!filamentDirty(profile, edit) || !edit.name.trim()) return;
    const parsedCost = parseOptionalNumber(edit.cost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) { toast.error("Invalid filament cost"); return; }
    const key = `f${profile.id}`;
    setRowStatus((s) => ({ ...s, [key]: "saving" }));
    const payload = {
      name: edit.name.trim(),
      material_type: edit.materialType.trim() || null,
      material_brand: edit.materialBrand.trim() || null,
      cost_per_kg: parsedCost,
      notes: edit.notes.trim() || null,
    };
    try {
      await updateFilamentProfile(profile.id, payload);
      const saved = { ...profile, ...payload };
      setFilaments((cur) => cur.map((p) => (p.id === profile.id ? saved : p)));
      setFilamentEdits((cur) => ({ ...cur, [profile.id]: filamentEdit(saved) }));
      flashSaved(key);
    } catch (e: any) { setError(e.message); toast.error(e); clearStatus(key); }
  }

  async function handleDeleteFilament(id: number) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deleteFilamentProfile(id);
      toast.success("Filament preset removed");
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  async function handleCreatePrinter() {
    const trimmedName = newPrinterName.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const parsedNozzle = parseOptionalNumber(newPrinterNozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) { toast.error("Invalid nozzle diameter"); return; }
    try {
      await createPrinterProfile({
        name: trimmedName,
        printer_model: newPrinterModel.trim() || null,
        nozzle_diameter_mm: parsedNozzle,
        notes: newPrinterNotes.trim() || null,
      });
      setNewPrinterName(""); setNewPrinterModel(""); setNewPrinterNozzle(""); setNewPrinterNotes("");
      setShowAddPrinter(false);
      toast.success(`Printer preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  async function autoSavePrinter(profile: PrinterProfileRead) {
    if (!auth.isAuthenticated) return;
    const edit = printerEdits[profile.id] ?? printerEdit(profile);
    if (!printerDirty(profile, edit) || !edit.name.trim()) return;
    const parsedNozzle = parseOptionalNumber(edit.nozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) { toast.error("Invalid nozzle diameter"); return; }
    const key = `p${profile.id}`;
    setRowStatus((s) => ({ ...s, [key]: "saving" }));
    const payload = {
      name: edit.name.trim(),
      printer_model: edit.model.trim() || null,
      nozzle_diameter_mm: parsedNozzle,
      notes: edit.notes.trim() || null,
    };
    try {
      await updatePrinterProfile(profile.id, payload);
      const saved = { ...profile, ...payload };
      setPrinters((cur) => cur.map((p) => (p.id === profile.id ? saved : p)));
      setPrinterEdits((cur) => ({ ...cur, [profile.id]: printerEdit(saved) }));
      flashSaved(key);
    } catch (e: any) { setError(e.message); toast.error(e); clearStatus(key); }
  }

  async function handleDeletePrinter(id: number) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deletePrinterProfile(id);
      toast.success("Printer preset removed");
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  return (
    <div className="animate-panel-in space-y-6">
      {error && (
        <div className="rounded border border-destructive/20 bg-destructive/5 px-4 py-3 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* ── Filament presets ─────────────────────────────────────── */}
      <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        {/* header */}
        <div className="flex items-center justify-between border-b border-border bg-muted/50 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-accent">
              <Layers className="h-4 w-4 text-primary" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-foreground">Filament presets</h3>
                <span className="rounded-full bg-muted px-2 py-0.5 text-3xs font-semibold text-muted-foreground">
                  {filaments.length}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">Material types, brands, and cost per kg</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {spoolmanEnabled && (
              <button
                type="button"
                onClick={handleSyncSpoolman}
                disabled={!auth.isAuthenticated || syncing}
                title="Import and refresh presets from Spoolman"
                className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-40"
              >
                {syncing ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                Sync from Spoolman
              </button>
            )}
            <button
              type="button"
              onClick={() => {
                if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                setShowAddFilament((v) => !v);
              }}
              disabled={!auth.isAuthenticated}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-40"
            >
              <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showAddFilament ? "rotate-180" : ""}`} />
              Add preset
            </button>
          </div>
        </div>

        {/* add form */}
        {showAddFilament && (
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateFilament(); }}
            className="border-b border-border bg-accent/30 px-5 py-4"
          >
            <p className="mb-3 text-3xs font-semibold uppercase tracking-wider text-muted-foreground">New filament preset</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_7rem_1fr_7rem_1fr_auto]">
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Preset name *" className={formInputClass} autoFocus />
              <input value={newType} onChange={(e) => setNewType(e.target.value)} placeholder="Type (PLA…)" className={formInputClass} />
              <input value={newBrand} onChange={(e) => setNewBrand(e.target.value)} placeholder="Brand" className={formInputClass} />
              <input value={newCost} onChange={(e) => setNewCost(e.target.value)} inputMode="decimal" placeholder="$/kg" className={formInputClass} />
              <input value={newNotes} onChange={(e) => setNewNotes(e.target.value)} placeholder="Notes" className={formInputClass} />
              <div className="flex gap-1.5">
                <button
                  type="submit"
                  disabled={!newName.trim()}
                  className="inline-flex h-8 flex-1 items-center justify-center gap-1 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:bg-primary-hover disabled:opacity-40 sm:flex-none sm:w-20"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddFilament(false)}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </form>
        )}

        {/* list */}
        <div>
          {loading ? (
            <div className="space-y-px p-5">
              {[1, 2, 3].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-muted" />
              ))}
            </div>
          ) : filaments.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <Layers className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm font-medium text-muted-foreground">No filament presets yet</p>
              <p className="text-xs text-muted-foreground/60">Add a preset to track material costs and types</p>
            </div>
          ) : (
            <div>
              {/* column labels */}
              <div className="hidden grid-cols-[1fr_7rem_1fr_7rem_1fr_auto] items-center gap-2 border-b border-border px-5 py-2 sm:grid">
                <ColLabel className="pl-[1.875rem]">Name</ColLabel>
                <ColLabel className="pl-2.5">Type</ColLabel>
                <ColLabel className="pl-2.5">Brand</ColLabel>
                <ColLabel className="pl-2.5">$/kg</ColLabel>
                <ColLabel className="pl-2.5">Notes</ColLabel>
                <span className="w-[4.5rem]" />
              </div>
              <div className="divide-y divide-border">
                {filaments.map((profile) => {
                  const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
                  const linked = profile.spoolman_filament_id != null;
                  const locked = !auth.isAuthenticated || linked;
                  return (
                    <div
                      key={profile.id}
                      onBlur={(e) => handleRowBlur(e, () => autoSaveFilament(profile))}
                      className="group grid grid-cols-1 items-center gap-2 px-5 py-3 transition-colors hover:bg-muted/30 sm:grid-cols-[1fr_7rem_1fr_7rem_1fr_auto]"
                    >
                      <div>
                        <div className="flex items-center gap-2.5">
                          <span
                            className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${edit.materialType ? materialColor(edit.materialType) : "bg-muted-foreground/30"}`}
                            title={edit.materialType || "Unknown type"}
                          />
                          <input
                            value={edit.name}
                            onChange={(e) => updateFilamentEdit(profile.id, { name: e.target.value })}
                            disabled={locked}
                            aria-label={`Filament preset name ${profile.id}`}
                            placeholder="Preset name"
                            className={inputClass}
                          />
                          {linked && (
                            <span
                              title="Synced from Spoolman — edit it in Spoolman"
                              className="flex-shrink-0 rounded-full border border-emerald-500/40 px-1.5 py-0.5 text-3xs font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400"
                            >
                              Spoolman
                            </span>
                          )}
                        </div>
                        {profile.usage_count > 0 && (
                          <p className="mt-1 truncate pl-5 text-3xs text-muted-foreground">
                            Used by {profile.usage_count} file{profile.usage_count === 1 ? "" : "s"}
                          </p>
                        )}
                      </div>
                      <input
                        value={edit.materialType}
                        onChange={(e) => updateFilamentEdit(profile.id, { materialType: e.target.value })}
                        disabled={locked}
                        aria-label={`Filament type ${profile.id}`}
                        placeholder="PLA, PETG…"
                        className={inputClass}
                      />
                      <input
                        value={edit.materialBrand}
                        onChange={(e) => updateFilamentEdit(profile.id, { materialBrand: e.target.value })}
                        disabled={locked}
                        aria-label={`Filament brand ${profile.id}`}
                        placeholder="Brand"
                        className={inputClass}
                      />
                      <input
                        value={edit.cost}
                        onChange={(e) => updateFilamentEdit(profile.id, { cost: e.target.value })}
                        disabled={locked}
                        inputMode="decimal"
                        aria-label={`Filament cost per kg ${profile.id}`}
                        placeholder="0.00"
                        className={inputClass}
                      />
                      <input
                        value={edit.notes}
                        onChange={(e) => updateFilamentEdit(profile.id, { notes: e.target.value })}
                        disabled={locked}
                        aria-label={`Filament notes ${profile.id}`}
                        placeholder="Notes"
                        className={inputClass}
                      />
                      <div className="flex h-8 w-[4.5rem] items-center justify-end gap-1">
                        <RowStatus state={rowStatus[`f${profile.id}`]} />
                        {!linked && (
                          <button
                            onClick={() => handleDeleteFilament(profile.id)}
                            disabled={!auth.isAuthenticated}
                            title="Delete"
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-muted-foreground/50 opacity-0 transition-[opacity,color,background-color,border-color] hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 group-hover:opacity-100 disabled:opacity-40 disabled:hover:border-transparent"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </section>

      {/* ── Printer presets ──────────────────────────────────────── */}
      <section className="overflow-hidden rounded-lg border border-border bg-card shadow-sm">
        {/* header */}
        <div className="flex items-center justify-between border-b border-border bg-muted/50 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-muted text-muted-foreground">
              <Printer className="h-4 w-4" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-foreground">Printer presets</h3>
                <span className="rounded-full bg-muted px-2 py-0.5 text-3xs font-semibold text-muted-foreground">
                  {printers.length}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">Nozzle sizes and slicer configurations</p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
              setShowAddPrinter((v) => !v);
            }}
            disabled={!auth.isAuthenticated}
            className="inline-flex items-center gap-1.5 rounded-md border border-border bg-background px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-muted disabled:opacity-40"
          >
            <ChevronDown className={`h-3.5 w-3.5 transition-transform ${showAddPrinter ? "rotate-180" : ""}`} />
            Add preset
          </button>
        </div>

        {/* add form */}
        {showAddPrinter && (
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreatePrinter(); }}
            className="border-b border-border bg-accent/30 px-5 py-4"
          >
            <p className="mb-3 text-3xs font-semibold uppercase tracking-wider text-muted-foreground">New printer preset</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1.4fr_1fr_1fr_6rem_auto]">
              <input value={newPrinterName} onChange={(e) => setNewPrinterName(e.target.value)} placeholder="Preset name *" className={formInputClass} autoFocus />
              <input value={newPrinterModel} onChange={(e) => setNewPrinterModel(e.target.value)} placeholder="Printer model" className={formInputClass} />
              <input value={newPrinterNotes} onChange={(e) => setNewPrinterNotes(e.target.value)} placeholder="Notes" className={formInputClass} />
              <input value={newPrinterNozzle} onChange={(e) => setNewPrinterNozzle(e.target.value)} inputMode="decimal" placeholder="Nozzle mm" className={formInputClass} />
              <div className="flex gap-1.5">
                <button
                  type="submit"
                  disabled={!newPrinterName.trim()}
                  className="inline-flex h-8 flex-1 items-center justify-center gap-1 rounded-md bg-primary px-3 text-xs font-medium text-primary-foreground transition-opacity hover:bg-primary-hover disabled:opacity-40 sm:flex-none sm:w-20"
                >
                  <Plus className="h-3.5 w-3.5" />
                  Add
                </button>
                <button
                  type="button"
                  onClick={() => setShowAddPrinter(false)}
                  className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </form>
        )}

        {/* list */}
        <div>
          {loading ? (
            <div className="space-y-px p-5">
              {[1, 2].map((i) => (
                <div key={i} className="h-10 animate-pulse rounded bg-muted" />
              ))}
            </div>
          ) : printers.length === 0 ? (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <Printer className="h-8 w-8 text-muted-foreground/30" />
              <p className="text-sm font-medium text-muted-foreground">No printer presets yet</p>
              <p className="text-xs text-muted-foreground/60">Add a preset to store nozzle configs and notes</p>
            </div>
          ) : (
            <div>
              {/* column labels */}
              <div className="hidden grid-cols-[1.4fr_1fr_1fr_6rem_auto] items-center gap-2 border-b border-border px-5 py-2 sm:grid">
                <ColLabel className="pl-2.5">Preset name</ColLabel>
                <ColLabel className="pl-2.5">Printer model</ColLabel>
                <ColLabel className="pl-2.5">Notes</ColLabel>
                <ColLabel className="pl-2.5">Nozzle mm</ColLabel>
                <span className="w-[4.5rem]" />
              </div>
              <div className="divide-y divide-border">
                {printers.map((profile) => {
                  const edit = printerEdits[profile.id] ?? printerEdit(profile);
                  return (
                    <div
                      key={profile.id}
                      onBlur={(e) => handleRowBlur(e, () => autoSavePrinter(profile))}
                      className="group grid grid-cols-1 items-start gap-2 px-5 py-3 transition-colors hover:bg-muted/30 sm:grid-cols-[1.4fr_1fr_1fr_6rem_auto] sm:items-center"
                    >
                      <div>
                        <input
                          value={edit.name}
                          onChange={(e) => updatePrinterEdit(profile.id, { name: e.target.value })}
                          disabled={!auth.isAuthenticated}
                          aria-label={`Printer preset name ${profile.id}`}
                          placeholder="Preset name"
                          className={inputClass}
                        />
                        {(profile.slicer_name || profile.usage_count > 0) && (
                          <p className="mt-1 truncate pl-0.5 text-3xs text-muted-foreground">
                            {[
                              profile.slicer_name ? `Detected from ${profile.slicer_name}` : null,
                              profile.usage_count > 0
                                ? `used by ${profile.usage_count} file${profile.usage_count === 1 ? "" : "s"}`
                                : null,
                            ].filter(Boolean).join(" · ")}
                          </p>
                        )}
                      </div>
                      <input
                        value={edit.model}
                        onChange={(e) => updatePrinterEdit(profile.id, { model: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        aria-label={`Printer model ${profile.id}`}
                        placeholder="Printer model"
                        className={inputClass}
                      />
                      <input
                        value={edit.notes}
                        onChange={(e) => updatePrinterEdit(profile.id, { notes: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        aria-label={`Printer notes ${profile.id}`}
                        placeholder="Notes"
                        className={inputClass}
                      />
                      <input
                        value={edit.nozzle}
                        onChange={(e) => updatePrinterEdit(profile.id, { nozzle: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        inputMode="decimal"
                        aria-label={`Printer nozzle diameter ${profile.id}`}
                        placeholder="0.4"
                        className={inputClass}
                      />
                      <div className="flex h-8 w-[4.5rem] items-center justify-end gap-1">
                        <RowStatus state={rowStatus[`p${profile.id}`]} />
                        <button
                          onClick={() => handleDeletePrinter(profile.id)}
                          disabled={!auth.isAuthenticated}
                          title="Delete"
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-transparent text-muted-foreground/50 opacity-0 transition-[opacity,color,background-color,border-color] hover:border-destructive/40 hover:bg-destructive/10 hover:text-destructive focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 group-hover:opacity-100 disabled:opacity-40 disabled:hover:border-transparent"
                        >
                          <Trash2 className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
