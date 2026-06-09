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
  updateFilamentProfile,
  updatePrinterProfile,
} from "@/lib/api";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Layers, Plus, Printer, Save, Trash2, X, ChevronDown } from "lucide-react";

type FilamentEdit = {
  name: string;
  materialType: string;
  materialBrand: string;
  cost: string;
};

type PrinterEdit = {
  name: string;
  nozzle: string;
  notes: string;
};

const inputClass =
  "h-8 w-full rounded border border-border bg-background px-2.5 text-xs text-foreground outline-none transition-shadow placeholder:text-muted-foreground/60 focus:border-transparent focus:ring-2 focus:ring-ring disabled:opacity-40";

function parseOptionalNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

function formatCost(value: number | null): string {
  return value == null ? "" : String(Number(value.toFixed(4)));
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
  };
}

function printerEdit(profile: PrinterProfileRead): PrinterEdit {
  return {
    name: profile.name,
    nozzle: formatNozzle(profile.nozzle_diameter_mm),
    notes: profile.notes ?? "",
  };
}

const MATERIAL_COLORS: Record<string, string> = {
  pla: "bg-emerald-500",
  petg: "bg-blue-500 dark:bg-orange-500",
  abs: "bg-blue-500 dark:bg-orange-500",
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

function ColLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </span>
  );
}

export function FilamentProfilesCard() {
  const auth = useRequireAuth();
  const [filaments, setFilaments] = useState<FilamentProfileRead[]>([]);
  const [printers, setPrinters] = useState<PrinterProfileRead[]>([]);
  const [filamentEdits, setFilamentEdits] = useState<Record<number, FilamentEdit>>({});
  const [printerEdits, setPrinterEdits] = useState<Record<number, PrinterEdit>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // add form state — filament
  const [showAddFilament, setShowAddFilament] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState("");
  const [newBrand, setNewBrand] = useState("");
  const [newCost, setNewCost] = useState("");

  // add form state — printer
  const [showAddPrinter, setShowAddPrinter] = useState(false);
  const [newPrinterName, setNewPrinterName] = useState("");
  const [newPrinterModel, setNewPrinterModel] = useState("");
  const [newPrinterNozzle, setNewPrinterNozzle] = useState("");

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
      [id]: { ...(cur[id] ?? { name: "", materialType: "", materialBrand: "", cost: "" }), ...patch },
    }));
  }

  function updatePrinterEdit(id: number, patch: Partial<PrinterEdit>) {
    setPrinterEdits((cur) => ({
      ...cur,
      [id]: { ...(cur[id] ?? { name: "", nozzle: "", notes: "" }), ...patch },
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
      });
      setNewName(""); setNewType(""); setNewBrand(""); setNewCost("");
      setShowAddFilament(false);
      toast.success(`Filament preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  async function handleSaveFilament(profile: FilamentProfileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
    if (!edit.name.trim()) return;
    const parsedCost = parseOptionalNumber(edit.cost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) { toast.error("Invalid filament cost"); return; }
    try {
      await updateFilamentProfile(profile.id, {
        name: edit.name.trim(),
        material_type: edit.materialType.trim() || null,
        material_brand: edit.materialBrand.trim() || null,
        cost_per_kg: parsedCost,
      });
      toast.success("Filament preset saved");
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
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
      });
      setNewPrinterName(""); setNewPrinterModel(""); setNewPrinterNozzle("");
      setShowAddPrinter(false);
      toast.success(`Printer preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
  }

  async function handleSavePrinter(profile: PrinterProfileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const edit = printerEdits[profile.id] ?? printerEdit(profile);
    if (!edit.name.trim()) return;
    const parsedNozzle = parseOptionalNumber(edit.nozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) { toast.error("Invalid nozzle diameter"); return; }
    try {
      await updatePrinterProfile(profile.id, {
        name: edit.name.trim(),
        nozzle_diameter_mm: parsedNozzle,
        notes: edit.notes.trim() || null,
      });
      toast.success("Printer preset saved");
      refresh();
    } catch (e: any) { setError(e.message); toast.error(e); }
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
    <div className="space-y-6">
      {error && (
        <div className="rounded border border-destructive/20 bg-destructive/5 px-4 py-3 text-xs text-destructive">
          {error}
        </div>
      )}

      {/* ── Filament presets ─────────────────────────────────────── */}
      <section className="overflow-hidden rounded-lg border border-border bg-background">
        {/* header */}
        <div className="flex items-center justify-between border-b border-border bg-muted/40 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-blue-50 dark:bg-orange-950/40">
              <Layers className="h-4 w-4 text-blue-600 dark:text-orange-500" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-foreground">Filament presets</h3>
                <span className="rounded-full bg-blue-100 dark:bg-orange-900/50 dark:bg-orange-950/50 px-2 py-0.5 text-[10px] font-semibold text-blue-700 dark:text-orange-400 dark:text-orange-400">
                  {filaments.length}
                </span>
              </div>
              <p className="text-xs text-muted-foreground">Material types, brands, and cost per kg</p>
            </div>
          </div>
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

        {/* add form */}
        {showAddFilament && (
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateFilament(); }}
            className="border-b border-border bg-accent/30 px-5 py-4"
          >
            <p className="mb-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">New filament preset</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_7rem_1fr_7rem_auto]">
              <input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Preset name *" className={inputClass} autoFocus />
              <input value={newType} onChange={(e) => setNewType(e.target.value)} placeholder="Type (PLA…)" className={inputClass} />
              <input value={newBrand} onChange={(e) => setNewBrand(e.target.value)} placeholder="Brand" className={inputClass} />
              <input value={newCost} onChange={(e) => setNewCost(e.target.value)} inputMode="decimal" placeholder="$/kg" className={inputClass} />
              <div className="flex gap-1.5">
                <button
                  type="submit"
                  disabled={!newName.trim()}
                  className="inline-flex h-8 flex-1 items-center justify-center gap-1 rounded-md bg-blue-600 dark:bg-orange-600 px-3 text-xs font-medium text-white transition-opacity hover:bg-blue-700 dark:hover:bg-orange-700 disabled:opacity-40 sm:flex-none sm:w-20"
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
              <div className="hidden grid-cols-[1fr_7rem_1fr_7rem_auto] items-center gap-2 border-b border-border px-5 py-2 sm:grid">
                <ColLabel>Name</ColLabel>
                <ColLabel>Type</ColLabel>
                <ColLabel>Brand</ColLabel>
                <ColLabel>$/kg</ColLabel>
                <span className="w-[4.5rem]" />
              </div>
              <div className="divide-y divide-border">
                {filaments.map((profile) => {
                  const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
                  return (
                    <div key={profile.id} className="group grid grid-cols-1 items-center gap-2 px-5 py-3 transition-colors hover:bg-muted/30 sm:grid-cols-[1fr_7rem_1fr_7rem_auto]">
                      <div className="flex items-center gap-2.5">
                        <span
                          className={`h-2.5 w-2.5 flex-shrink-0 rounded-full ${edit.materialType ? materialColor(edit.materialType) : "bg-slate-300 dark:bg-slate-600"}`}
                          title={edit.materialType || "Unknown type"}
                        />
                        <input
                          value={edit.name}
                          onChange={(e) => updateFilamentEdit(profile.id, { name: e.target.value })}
                          disabled={!auth.isAuthenticated}
                          aria-label={`Filament preset name ${profile.id}`}
                          placeholder="Preset name"
                          className={inputClass}
                        />
                      </div>
                      <input
                        value={edit.materialType}
                        onChange={(e) => updateFilamentEdit(profile.id, { materialType: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        aria-label={`Filament type ${profile.id}`}
                        placeholder="PLA, PETG…"
                        className={inputClass}
                      />
                      <input
                        value={edit.materialBrand}
                        onChange={(e) => updateFilamentEdit(profile.id, { materialBrand: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        aria-label={`Filament brand ${profile.id}`}
                        placeholder="Brand"
                        className={inputClass}
                      />
                      <input
                        value={edit.cost}
                        onChange={(e) => updateFilamentEdit(profile.id, { cost: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        inputMode="decimal"
                        aria-label={`Filament cost per kg ${profile.id}`}
                        placeholder="0.00"
                        className={inputClass}
                      />
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleSaveFilament(profile)}
                          disabled={!auth.isAuthenticated}
                          title="Save"
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-blue-300 dark:hover:border-orange-600 hover:bg-blue-50 hover:text-blue-600 dark:text-orange-500 disabled:opacity-40 dark:hover:bg-orange-950/40"
                        >
                          <Save className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => handleDeleteFilament(profile.id)}
                          disabled={!auth.isAuthenticated}
                          title="Delete"
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600 disabled:opacity-40 dark:hover:bg-red-950/40"
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

      {/* ── Printer presets ──────────────────────────────────────── */}
      <section className="overflow-hidden rounded-lg border border-border bg-background">
        {/* header */}
        <div className="flex items-center justify-between border-b border-border bg-muted/40 px-5 py-4">
          <div className="flex items-center gap-3">
            <div className="flex h-8 w-8 items-center justify-center rounded-md bg-muted dark:bg-slate-800">
              <Printer className="h-4 w-4 text-muted-foreground dark:text-muted-foreground" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h3 className="text-sm font-semibold text-foreground">Printer presets</h3>
                <span className="rounded-full bg-muted dark:bg-slate-800 px-2 py-0.5 text-[10px] font-semibold text-muted-foreground dark:text-muted-foreground">
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
            <p className="mb-3 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">New printer preset</p>
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1fr_7rem_auto]">
              <input value={newPrinterName} onChange={(e) => setNewPrinterName(e.target.value)} placeholder="Preset name *" className={inputClass} autoFocus />
              <input value={newPrinterModel} onChange={(e) => setNewPrinterModel(e.target.value)} placeholder="Printer model" className={inputClass} />
              <input value={newPrinterNozzle} onChange={(e) => setNewPrinterNozzle(e.target.value)} inputMode="decimal" placeholder="Nozzle mm" className={inputClass} />
              <div className="flex gap-1.5">
                <button
                  type="submit"
                  disabled={!newPrinterName.trim()}
                  className="inline-flex h-8 flex-1 items-center justify-center gap-1 rounded-md bg-blue-600 dark:bg-orange-600 px-3 text-xs font-medium text-white transition-opacity hover:bg-blue-700 dark:hover:bg-orange-700 disabled:opacity-40 sm:flex-none sm:w-20"
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
              <div className="hidden grid-cols-[1fr_1fr_7rem_auto] items-center gap-2 border-b border-border px-5 py-2 sm:grid">
                <ColLabel>Name</ColLabel>
                <ColLabel>Model / Slicer</ColLabel>
                <ColLabel>Nozzle mm</ColLabel>
                <span className="w-[4.5rem]" />
              </div>
              <div className="divide-y divide-border">
                {printers.map((profile) => {
                  const edit = printerEdits[profile.id] ?? printerEdit(profile);
                  return (
                    <div key={profile.id} className="group grid grid-cols-1 items-start gap-2 px-5 py-3 transition-colors hover:bg-muted/30 sm:grid-cols-[1fr_1fr_7rem_auto] sm:items-center">
                      <div>
                        <input
                          value={edit.name}
                          onChange={(e) => updatePrinterEdit(profile.id, { name: e.target.value })}
                          disabled={!auth.isAuthenticated}
                          aria-label={`Printer preset name ${profile.id}`}
                          placeholder="Preset name"
                          className={inputClass}
                        />
                        {(profile.printer_model || profile.slicer_name) && (
                          <p className="mt-1 truncate pl-0.5 text-[10px] text-muted-foreground">
                            {[profile.printer_model, profile.slicer_name].filter(Boolean).join(" · ")}
                          </p>
                        )}
                      </div>
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
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => handleSavePrinter(profile)}
                          disabled={!auth.isAuthenticated}
                          title="Save"
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-blue-300 dark:hover:border-orange-600 hover:bg-blue-50 hover:text-blue-600 dark:text-orange-500 disabled:opacity-40 dark:hover:bg-orange-950/40"
                        >
                          <Save className="h-3.5 w-3.5" />
                        </button>
                        <button
                          onClick={() => handleDeletePrinter(profile.id)}
                          disabled={!auth.isAuthenticated}
                          title="Delete"
                          className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-border text-muted-foreground transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600 disabled:opacity-40 dark:hover:bg-red-950/40"
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
