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
import { DollarSign, Plus, Printer, Save, X } from "lucide-react";

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
  "h-9 w-full rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)] px-3 text-xs font-mono text-[var(--on-surface)] outline-none transition-shadow placeholder:text-[var(--on-surface-variant)]/60 focus:border-transparent focus:ring-2 focus:ring-[var(--primary)] disabled:opacity-50";

const iconButtonClass =
  "inline-flex h-9 w-9 flex-shrink-0 items-center justify-center rounded border border-[var(--outline-variant)] text-[var(--on-surface-variant)] transition-colors hover:bg-[var(--surface-container-low)] disabled:opacity-50";

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

export function FilamentProfilesCard() {
  const auth = useRequireAuth();
  const [filaments, setFilaments] = useState<FilamentProfileRead[]>([]);
  const [printers, setPrinters] = useState<PrinterProfileRead[]>([]);
  const [name, setName] = useState("");
  const [materialType, setMaterialType] = useState("");
  const [materialBrand, setMaterialBrand] = useState("");
  const [costPerKg, setCostPerKg] = useState("");
  const [printerName, setPrinterName] = useState("");
  const [printerModel, setPrinterModel] = useState("");
  const [printerNozzle, setPrinterNozzle] = useState("");
  const [filamentEdits, setFilamentEdits] = useState<Record<number, FilamentEdit>>({});
  const [printerEdits, setPrinterEdits] = useState<Record<number, PrinterEdit>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refresh() {
    try {
      const [nextFilaments, nextPrinters] = await Promise.all([
        listFilamentProfiles(),
        listPrinterProfiles(),
      ]);
      setFilaments(nextFilaments);
      setPrinters(nextPrinters);
      setFilamentEdits(
        Object.fromEntries(
          nextFilaments.map((profile) => [profile.id, filamentEdit(profile)]),
        ),
      );
      setPrinterEdits(
        Object.fromEntries(
          nextPrinters.map((profile) => [profile.id, printerEdit(profile)]),
        ),
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
    setFilamentEdits((current) => ({
      ...current,
      [id]: {
        ...(current[id] ?? {
          name: "",
          materialType: "",
          materialBrand: "",
          cost: "",
        }),
        ...patch,
      },
    }));
  }

  function updatePrinterEdit(id: number, patch: Partial<PrinterEdit>) {
    setPrinterEdits((current) => ({
      ...current,
      [id]: {
        ...(current[id] ?? {
          name: "",
          nozzle: "",
          notes: "",
        }),
        ...patch,
      },
    }));
  }

  async function handleCreateFilament() {
    const trimmedName = name.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }

    const parsedCost = parseOptionalNumber(costPerKg);
    if (parsedCost !== null && Number.isNaN(parsedCost)) {
      toast.error("Invalid filament cost");
      return;
    }

    try {
      await createFilamentProfile({
        name: trimmedName,
        material_type: materialType.trim() || null,
        material_brand: materialBrand.trim() || null,
        cost_per_kg: parsedCost,
      });
      setName("");
      setMaterialType("");
      setMaterialBrand("");
      setCostPerKg("");
      toast.success(`Filament preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleSaveFilament(profile: FilamentProfileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
    if (!edit.name.trim()) return;
    const parsedCost = parseOptionalNumber(edit.cost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) {
      toast.error("Invalid filament cost");
      return;
    }

    try {
      await updateFilamentProfile(profile.id, {
        name: edit.name.trim(),
        material_type: edit.materialType.trim() || null,
        material_brand: edit.materialBrand.trim() || null,
        cost_per_kg: parsedCost,
      });
      toast.success("Filament preset saved");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDeleteFilament(id: number) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deleteFilamentProfile(id);
      toast.success("Filament preset removed");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleCreatePrinter() {
    const trimmedName = printerName.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }

    const parsedNozzle = parseOptionalNumber(printerNozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) {
      toast.error("Invalid nozzle diameter");
      return;
    }

    try {
      await createPrinterProfile({
        name: trimmedName,
        printer_model: printerModel.trim() || null,
        nozzle_diameter_mm: parsedNozzle,
      });
      setPrinterName("");
      setPrinterModel("");
      setPrinterNozzle("");
      toast.success(`Printer preset "${trimmedName}" saved`);
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleSavePrinter(profile: PrinterProfileRead) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    const edit = printerEdits[profile.id] ?? printerEdit(profile);
    if (!edit.name.trim()) return;
    const parsedNozzle = parseOptionalNumber(edit.nozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) {
      toast.error("Invalid nozzle diameter");
      return;
    }
    try {
      await updatePrinterProfile(profile.id, {
        name: edit.name.trim(),
        nozzle_diameter_mm: parsedNozzle,
        notes: edit.notes.trim() || null,
      });
      toast.success("Printer preset saved");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  async function handleDeletePrinter(id: number) {
    if (!auth.isAuthenticated) { auth.showAuthRequiredToast(); return; }
    try {
      await deletePrinterProfile(id);
      toast.success("Printer preset removed");
      refresh();
    } catch (e: any) {
      setError(e.message);
      toast.error(e);
    }
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded border border-[var(--error)]/30 bg-[var(--error-container)]/20 p-2 text-xs text-[var(--error)] font-mono">
          {error}
        </div>
      )}

      <section className="overflow-hidden rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)]">
        <div className="flex flex-col gap-3 border-b border-[var(--outline-variant)] px-4 py-4 sm:px-6">
          <div className="flex items-center gap-2">
            <DollarSign className="h-4 w-4 flex-shrink-0 text-[var(--on-surface-variant)]" />
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">
              Filament presets
            </h3>
            <span className="font-mono text-xs text-[var(--on-surface-variant)]">
              ({filaments.length})
            </span>
          </div>
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreateFilament(); }}
            className="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(12rem,1.2fr)_8rem_minmax(10rem,1fr)_8rem_auto]"
          >
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={!auth.isAuthenticated}
              aria-label="New filament preset name"
              placeholder={auth.isAuthenticated ? "Preset name..." : "Sign in to add"}
              className={inputClass}
            />
            <input
              value={materialType}
              onChange={(e) => setMaterialType(e.target.value)}
              disabled={!auth.isAuthenticated}
              aria-label="New filament type"
              placeholder="Type"
              className={inputClass}
            />
            <input
              value={materialBrand}
              onChange={(e) => setMaterialBrand(e.target.value)}
              disabled={!auth.isAuthenticated}
              aria-label="New filament brand"
              placeholder="Brand"
              className={inputClass}
            />
            <input
              value={costPerKg}
              onChange={(e) => setCostPerKg(e.target.value)}
              disabled={!auth.isAuthenticated}
              inputMode="decimal"
              aria-label="New filament cost per kilogram"
              placeholder="Cost/kg"
              className={inputClass}
            />
            <button
              type="submit"
              disabled={!name.trim() || !auth.isAuthenticated}
              className="inline-flex h-9 w-9 items-center justify-center rounded bg-[var(--primary)] text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
              title="Add filament preset"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
          ) : filaments.length === 0 ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">
              No filament presets saved yet.
            </p>
          ) : (
            <div className="space-y-2">
              {filaments.map((profile) => {
                const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
                return (
                  <div
                    key={profile.id}
                    className="grid grid-cols-1 gap-2 rounded border border-transparent px-2 py-2 transition-colors hover:border-[var(--outline-variant)] hover:bg-[var(--surface-container-low)] lg:grid-cols-[minmax(12rem,1.2fr)_8rem_minmax(10rem,1fr)_8rem_auto_auto]"
                  >
                    <input
                      value={edit.name}
                      onChange={(e) => updateFilamentEdit(profile.id, { name: e.target.value })}
                      disabled={!auth.isAuthenticated}
                      aria-label={`Filament preset name ${profile.id}`}
                      placeholder="Preset name"
                      className={inputClass}
                    />
                    <input
                      value={edit.materialType}
                      onChange={(e) => updateFilamentEdit(profile.id, { materialType: e.target.value })}
                      disabled={!auth.isAuthenticated}
                      aria-label={`Filament type ${profile.id}`}
                      placeholder="Type"
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
                      aria-label={`Filament cost per kilogram ${profile.id}`}
                      placeholder="Cost/kg"
                      className={inputClass}
                    />
                    <button
                      onClick={() => handleSaveFilament(profile)}
                      disabled={!auth.isAuthenticated}
                      className={iconButtonClass}
                      title="Save filament preset"
                    >
                      <Save className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleDeleteFilament(profile.id)}
                      disabled={!auth.isAuthenticated}
                      className={`${iconButtonClass} text-[var(--error)] hover:bg-[var(--error-container)]/30`}
                      title="Delete filament preset"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>

      <section className="overflow-hidden rounded border border-[var(--outline-variant)] bg-[var(--surface-container-lowest)]">
        <div className="flex flex-col gap-3 border-b border-[var(--outline-variant)] px-4 py-4 sm:px-6">
          <div className="flex items-center gap-2">
            <Printer className="h-4 w-4 flex-shrink-0 text-[var(--on-surface-variant)]" />
            <h3 className="text-sm font-semibold text-[var(--on-surface)]">
              Printer presets
            </h3>
            <span className="font-mono text-xs text-[var(--on-surface-variant)]">
              ({printers.length})
            </span>
          </div>
          <form
            onSubmit={(e) => { e.preventDefault(); handleCreatePrinter(); }}
            className="grid grid-cols-1 gap-2 lg:grid-cols-[minmax(12rem,1fr)_minmax(12rem,1fr)_8rem_auto]"
          >
            <input
              value={printerName}
              onChange={(e) => setPrinterName(e.target.value)}
              disabled={!auth.isAuthenticated}
              aria-label="New printer preset name"
              placeholder={auth.isAuthenticated ? "Preset name..." : "Sign in to add"}
              className={inputClass}
            />
            <input
              value={printerModel}
              onChange={(e) => setPrinterModel(e.target.value)}
              disabled={!auth.isAuthenticated}
              aria-label="New printer model"
              placeholder="Printer model"
              className={inputClass}
            />
            <input
              value={printerNozzle}
              onChange={(e) => setPrinterNozzle(e.target.value)}
              disabled={!auth.isAuthenticated}
              inputMode="decimal"
              aria-label="New printer nozzle diameter"
              placeholder="Nozzle mm"
              className={inputClass}
            />
            <button
              type="submit"
              disabled={!printerName.trim() || !auth.isAuthenticated}
              className="inline-flex h-9 w-9 items-center justify-center rounded bg-[var(--primary)] text-[var(--primary-foreground)] transition-opacity hover:opacity-90 disabled:opacity-50"
              title="Add printer preset"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          </form>
        </div>

        <div className="p-3 sm:p-4">
          {loading ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">Loading...</p>
          ) : printers.length === 0 ? (
            <p className="text-xs text-[var(--on-surface-variant)] font-mono">
              No printer presets saved yet.
            </p>
          ) : (
            <div className="space-y-2">
              {printers.map((profile) => {
                const edit = printerEdits[profile.id] ?? printerEdit(profile);
                return (
                  <div
                    key={profile.id}
                    className="grid grid-cols-1 gap-2 rounded border border-transparent px-2 py-2 transition-colors hover:border-[var(--outline-variant)] hover:bg-[var(--surface-container-low)] lg:grid-cols-[minmax(12rem,1fr)_8rem_minmax(10rem,1fr)_auto_auto]"
                  >
                    <div className="min-w-0">
                      <input
                        value={edit.name}
                        onChange={(e) => updatePrinterEdit(profile.id, { name: e.target.value })}
                        disabled={!auth.isAuthenticated}
                        aria-label={`Printer preset name ${profile.id}`}
                        placeholder="Preset name"
                        className={inputClass}
                      />
                      <p className="mt-1 truncate font-mono text-[10px] text-[var(--on-surface-variant)]">
                        {[profile.printer_model, profile.slicer_name].filter(Boolean).join(" · ") || "No printer data"}
                      </p>
                    </div>
                    <input
                      value={edit.nozzle}
                      onChange={(e) => updatePrinterEdit(profile.id, { nozzle: e.target.value })}
                      disabled={!auth.isAuthenticated}
                      inputMode="decimal"
                      aria-label={`Printer nozzle diameter ${profile.id}`}
                      placeholder="Nozzle mm"
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
                    <button
                      onClick={() => handleSavePrinter(profile)}
                      disabled={!auth.isAuthenticated}
                      className={iconButtonClass}
                      title="Save printer preset"
                    >
                      <Save className="h-3.5 w-3.5" />
                    </button>
                    <button
                      onClick={() => handleDeletePrinter(profile.id)}
                      disabled={!auth.isAuthenticated}
                      className={`${iconButtonClass} text-[var(--error)] hover:bg-[var(--error-container)]/30`}
                      title="Delete printer preset"
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
