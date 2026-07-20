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
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import { Localized } from "@/components/ui/localized";
import { useI18n } from "@/lib/i18n";
import { translateUiText } from "@/components/ui/localized";

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

const compactInputClass = "h-9 text-sm";

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

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-1.5 block text-3xs font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </span>
  );
}

function RowLabel({ children }: { children: React.ReactNode }) {
  return (
    <span className="mb-1.5 block text-3xs font-semibold uppercase tracking-wider text-muted-foreground md:sr-only">
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
  const { locale } = useI18n();
  const [activeTab, setActiveTab] = useState<"filaments" | "printers">("filaments");
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

  const tabClass = "flex min-w-32 items-center justify-center gap-2 rounded-md px-4 py-2.5 text-sm font-medium text-muted-foreground transition-[background-color,color,transform] duration-press active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

  return (
    <Localized>
      <div className="animate-panel-in space-y-4">
      {error && (
        <div role="alert" className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <Card className="overflow-hidden">
        <div className="flex flex-col gap-3 border-b bg-muted/30 p-3 sm:flex-row sm:items-center sm:justify-between">
          <TabBar
            tabs={[
              { key: "filaments", label: <><Layers className="h-4 w-4" aria-hidden />{translateUiText(locale, "Filaments")} <Badge variant="secondary">{filaments.length}</Badge></> },
              { key: "printers", label: <><Printer className="h-4 w-4" aria-hidden />{translateUiText(locale, "Printers")} <Badge variant="secondary">{printers.length}</Badge></> },
            ]}
            active={activeTab}
            onChange={setActiveTab}
            showIndicator={false}
            className="gap-1 rounded-lg bg-background p-1 shadow-sm ring-1 ring-border"
            tabClassName={tabClass}
            activeTabClassName="bg-accent text-accent-foreground"
          />

          <div className="flex items-center justify-end gap-2">
            {activeTab === "filaments" && spoolmanEnabled && (
              <Button type="button" variant="outline" size="xs" onClick={handleSyncSpoolman} loading={syncing} disabled={!auth.isAuthenticated}>
                {!syncing && <RefreshCw className="h-3.5 w-3.5" />}
                {translateUiText(locale, "Sync Spoolman")}
              </Button>
            )}
            <Button
              type="button"
              size="xs"
              disabled={!auth.isAuthenticated}
              aria-expanded={activeTab === "filaments" ? showAddFilament : showAddPrinter}
              onClick={() => {
                if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
                if (activeTab === "filaments") setShowAddFilament((value) => !value);
                else setShowAddPrinter((value) => !value);
              }}
            >
              <Plus className="h-3.5 w-3.5" />
              {translateUiText(locale, activeTab === "filaments" ? "New filament" : "New printer")}
              <ChevronDown className={`h-3.5 w-3.5 transition-transform duration-press ${(activeTab === "filaments" ? showAddFilament : showAddPrinter) ? "rotate-180" : ""}`} />
            </Button>
          </div>
        </div>

        {activeTab === "filaments" ? (
          <section aria-labelledby="filament-presets-heading">
            <div className="flex items-center justify-between border-b px-5 py-3">
              <div>
                <h2 id="filament-presets-heading" className="text-sm font-semibold">Filament presets</h2>
                <p className="text-xs text-muted-foreground">Changes save automatically when leaving a row.</p>
              </div>
            </div>

            {showAddFilament && (
              <form onSubmit={(e) => { e.preventDefault(); handleCreateFilament(); }} aria-label="Create filament preset" className="border-b bg-accent/30 p-5">
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-5">
                  <label><FieldLabel>Name</FieldLabel><Input value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="Everyday PLA" className={compactInputClass} autoFocus required /></label>
                  <label><FieldLabel>Material</FieldLabel><Input value={newType} onChange={(e) => setNewType(e.target.value)} placeholder="PLA, PETG…" className={compactInputClass} /></label>
                  <label><FieldLabel>Brand</FieldLabel><Input value={newBrand} onChange={(e) => setNewBrand(e.target.value)} placeholder="Manufacturer" className={compactInputClass} /></label>
                  <label><FieldLabel>Cost per kg</FieldLabel><Input value={newCost} onChange={(e) => setNewCost(e.target.value)} inputMode="decimal" placeholder="0.00" className={compactInputClass} /></label>
                  <label><FieldLabel>Notes</FieldLabel><Input value={newNotes} onChange={(e) => setNewNotes(e.target.value)} placeholder="Optional" className={compactInputClass} /></label>
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <Button type="button" variant="ghost" size="xs" onClick={() => setShowAddFilament(false)}><X className="h-3.5 w-3.5" />Cancel</Button>
                  <Button type="submit" size="xs" disabled={!newName.trim()}><Plus className="h-3.5 w-3.5" />Add preset</Button>
                </div>
              </form>
            )}

            {loading ? (
              <div className="space-y-2 p-5">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div>
            ) : filaments.length === 0 ? (
              <EmptyState icon={Layers} title="No filament presets" description="Create one to track materials, brands, and costs." className="py-12" />
            ) : (
              <div>
                <div className="hidden grid-cols-[minmax(10rem,1.3fr)_7rem_minmax(8rem,1fr)_7rem_minmax(8rem,1fr)_3.5rem] gap-2 border-b bg-muted/20 px-5 py-2 md:grid">
                  {['Name', 'Material', 'Brand', 'Cost / kg', 'Notes', ''].map((label) => <span key={label || 'actions'} className="text-3xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>)}
                </div>
                <div className="divide-y">
                  {filaments.map((profile) => {
                    const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
                    const linked = profile.spoolman_filament_id != null;
                    const locked = !auth.isAuthenticated || linked;
                    return (
                      <div key={profile.id} onBlur={(e) => handleRowBlur(e, () => autoSaveFilament(profile))} className="group grid gap-3 px-5 py-4 transition-colors duration-press hover:bg-muted/20 md:grid-cols-[minmax(10rem,1.3fr)_7rem_minmax(8rem,1fr)_7rem_minmax(8rem,1fr)_3.5rem] md:items-start md:gap-2">
                        <label><RowLabel>Name</RowLabel><div className="flex items-center gap-2"><span className={`h-2.5 w-2.5 shrink-0 rounded-full ${edit.materialType ? materialColor(edit.materialType) : "bg-muted-foreground/30"}`} /><Input value={edit.name} onChange={(e) => updateFilamentEdit(profile.id, { name: e.target.value })} disabled={locked} aria-label={`Filament preset name ${profile.id}`} className={compactInputClass} /></div>{profile.usage_count > 0 && <span className="mt-1 block pl-[1.125rem] text-3xs text-muted-foreground">Used by {profile.usage_count} file{profile.usage_count === 1 ? "" : "s"}</span>}</label>
                        <label><RowLabel>Material</RowLabel><Input value={edit.materialType} onChange={(e) => updateFilamentEdit(profile.id, { materialType: e.target.value })} disabled={locked} aria-label={`Filament type ${profile.id}`} placeholder="PLA" className={compactInputClass} /></label>
                        <label><RowLabel>Brand</RowLabel><Input value={edit.materialBrand} onChange={(e) => updateFilamentEdit(profile.id, { materialBrand: e.target.value })} disabled={locked} aria-label={`Filament brand ${profile.id}`} placeholder="Brand" className={compactInputClass} /></label>
                        <label><RowLabel>Cost / kg</RowLabel><Input value={edit.cost} onChange={(e) => updateFilamentEdit(profile.id, { cost: e.target.value })} disabled={locked} inputMode="decimal" aria-label={`Filament cost per kg ${profile.id}`} placeholder="0.00" className={compactInputClass} /></label>
                        <label><RowLabel>Notes</RowLabel><Input value={edit.notes} onChange={(e) => updateFilamentEdit(profile.id, { notes: e.target.value })} disabled={locked} aria-label={`Filament notes ${profile.id}`} placeholder="Optional" className={compactInputClass} /></label>
                        <div className="flex h-9 items-center justify-end gap-1 md:mt-5">
                          {linked ? <Badge variant="success">Synced</Badge> : <><RowStatus state={rowStatus[`f${profile.id}`]} /><Button type="button" variant="ghost" size="icon-sm" onClick={() => handleDeleteFilament(profile.id)} disabled={!auth.isAuthenticated} aria-label={`Delete filament preset ${edit.name}`} title="Delete" className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></Button></>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </section>
        ) : (
          <section aria-labelledby="printer-presets-heading">
            <div className="border-b px-5 py-3">
              <h2 id="printer-presets-heading" className="text-sm font-semibold">Printer presets</h2>
              <p className="text-xs text-muted-foreground">Changes save automatically when leaving a row.</p>
            </div>

            {showAddPrinter && (
              <form onSubmit={(e) => { e.preventDefault(); handleCreatePrinter(); }} aria-label="Create printer preset" className="border-b bg-accent/30 p-5">
                <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
                  <label><FieldLabel>Name</FieldLabel><Input value={newPrinterName} onChange={(e) => setNewPrinterName(e.target.value)} placeholder="Voron 2.4 — 0.4 mm" className={compactInputClass} autoFocus required /></label>
                  <label><FieldLabel>Printer model</FieldLabel><Input value={newPrinterModel} onChange={(e) => setNewPrinterModel(e.target.value)} placeholder="Machine model" className={compactInputClass} /></label>
                  <label><FieldLabel>Nozzle diameter</FieldLabel><Input value={newPrinterNozzle} onChange={(e) => setNewPrinterNozzle(e.target.value)} inputMode="decimal" placeholder="0.4" className={compactInputClass} /></label>
                  <label><FieldLabel>Notes</FieldLabel><Input value={newPrinterNotes} onChange={(e) => setNewPrinterNotes(e.target.value)} placeholder="Optional" className={compactInputClass} /></label>
                </div>
                <div className="mt-4 flex justify-end gap-2">
                  <Button type="button" variant="ghost" size="xs" onClick={() => setShowAddPrinter(false)}><X className="h-3.5 w-3.5" />Cancel</Button>
                  <Button type="submit" size="xs" disabled={!newPrinterName.trim()}><Plus className="h-3.5 w-3.5" />Add preset</Button>
                </div>
              </form>
            )}

            {loading ? (
              <div className="space-y-2 p-5">{[1, 2, 3].map((i) => <Skeleton key={i} className="h-12" />)}</div>
            ) : printers.length === 0 ? (
              <EmptyState icon={Printer} title="No printer presets" description="Create one to reuse machine and nozzle settings." className="py-12" />
            ) : (
              <div>
                <div className="hidden grid-cols-[minmax(11rem,1.3fr)_minmax(10rem,1fr)_7rem_minmax(10rem,1fr)_3.5rem] gap-2 border-b bg-muted/20 px-5 py-2 md:grid">
                  {['Name', 'Printer model', 'Nozzle', 'Notes', ''].map((label) => <span key={label || 'actions'} className="text-3xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</span>)}
                </div>
                <div className="divide-y">
                  {printers.map((profile) => {
                    const edit = printerEdits[profile.id] ?? printerEdit(profile);
                    return (
                      <div key={profile.id} onBlur={(e) => handleRowBlur(e, () => autoSavePrinter(profile))} className="group grid gap-3 px-5 py-4 transition-colors duration-press hover:bg-muted/20 md:grid-cols-[minmax(11rem,1.3fr)_minmax(10rem,1fr)_7rem_minmax(10rem,1fr)_3.5rem] md:items-start md:gap-2">
                        <label><RowLabel>Name</RowLabel><Input value={edit.name} onChange={(e) => updatePrinterEdit(profile.id, { name: e.target.value })} disabled={!auth.isAuthenticated} aria-label={`Printer preset name ${profile.id}`} className={compactInputClass} />{(profile.slicer_name || profile.usage_count > 0) && <span className="mt-1 block text-3xs text-muted-foreground">{[profile.slicer_name ? `Detected from ${profile.slicer_name}` : null, profile.usage_count > 0 ? `used by ${profile.usage_count} file${profile.usage_count === 1 ? "" : "s"}` : null].filter(Boolean).join(" · ")}</span>}</label>
                        <label><RowLabel>Printer model</RowLabel><Input value={edit.model} onChange={(e) => updatePrinterEdit(profile.id, { model: e.target.value })} disabled={!auth.isAuthenticated} aria-label={`Printer model ${profile.id}`} placeholder="Machine model" className={compactInputClass} /></label>
                        <label><RowLabel>Nozzle</RowLabel><Input value={edit.nozzle} onChange={(e) => updatePrinterEdit(profile.id, { nozzle: e.target.value })} disabled={!auth.isAuthenticated} inputMode="decimal" aria-label={`Printer nozzle diameter ${profile.id}`} placeholder="0.4" className={compactInputClass} /></label>
                        <label><RowLabel>Notes</RowLabel><Input value={edit.notes} onChange={(e) => updatePrinterEdit(profile.id, { notes: e.target.value })} disabled={!auth.isAuthenticated} aria-label={`Printer notes ${profile.id}`} placeholder="Optional" className={compactInputClass} /></label>
                        <div className="flex h-9 items-center justify-end gap-1 md:mt-5"><RowStatus state={rowStatus[`p${profile.id}`]} /><Button type="button" variant="ghost" size="icon-sm" onClick={() => handleDeletePrinter(profile.id)} disabled={!auth.isAuthenticated} aria-label={`Delete printer preset ${edit.name}`} title="Delete" className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"><Trash2 className="h-3.5 w-3.5" /></Button></div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </section>
        )}
      </Card>
      </div>
    </Localized>
  );
}
