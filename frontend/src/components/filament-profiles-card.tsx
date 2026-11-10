"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  filamentProfilesOptions,
  printerProfilesOptions,
  useProfileCommands,
} from "@/lib/queries/profiles";
import { getSessionVersion } from "@/lib/session-transport";
import { ApiError, parseApiError, userMessage } from "@/lib/errors";
import { captureEditingBase } from "@/lib/api/editing";
import {
  FilamentProfileRead,
  PrinterProfileRead,
  FilamentProfileUpdate,
  PrinterProfileUpdate,
} from "@/types";

import { useSpoolmanStatus, useVaultConfig } from "@/lib/queries";
import { useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Check, Layers, Loader2, Plus, Printer, RefreshCw, Trash2, X, Pencil } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar } from "@/components/ui/tabs";
import { ProfileSettingsDetails } from "@/components/slicer-profile-details";
import { Localized } from "@/components/ui/localized";
import { useI18n } from "@/lib/i18n";
import { translateUiText } from "@/lib/locale";

type FilamentEdit = {
  name: string;
  materialType: string;
  materialBrand: string;
  cost: string;
  notes: string;
};

type FilamentDraft = FilamentEdit & { base: FilamentProfileRead };

type PrinterEdit = {
  name: string;
  model: string;
  nozzle: string;
  notes: string;
};

type PrinterDraft = PrinterEdit & { base: PrinterProfileRead };

type PresetReview<Row> = { phase: "required" | "loading" } | { phase: "ready"; snapshot: Row };

function needsPresetReview(parsed: ApiError): boolean {
  return (
    parsed.status === 412 ||
    parsed.status === 428 ||
    parsed.status === 0 ||
    parsed.status >= 500 ||
    parsed.code === "filament_profile_linked"
  );
}

type DeletePresetTarget = {
  kind: "filament" | "printer";
  id: number;
  name: string;
};

const compactInputClass = "h-10 text-sm";

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

function FieldLabel({ children }: { children: React.ReactNode }) {
  useUiLocale();
  return <span className="mb-1.5 block text-sm font-medium text-foreground">{children}</span>;
}

function RowLabel({ children }: { children: React.ReactNode }) {
  useUiLocale();
  return <span className="mb-1.5 block text-sm font-medium text-foreground">{children}</span>;
}

function RowStatus({ state }: { state?: "saving" | "saved" }) {
  useUiLocale();
  if (state === "saving")
    return <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />;
  if (state === "saved")
    return (
      <span role="status" aria-label={uiText("Saved")}>
        <Check className="h-3.5 w-3.5 text-emerald-500" aria-hidden />
      </span>
    );
  return null;
}

function PresetReviewPanel({
  review,
  busy,
  canSave,
  replaced,
  onReview,
  onAdopt,
  onSave,
}: {
  review: PresetReview<FilamentProfileRead | PrinterProfileRead>;
  busy: boolean;
  canSave: boolean;
  replaced: boolean;
  onReview: () => void;
  onAdopt: () => void;
  onSave: () => void;
}) {
  useUiLocale();
  const row = review.phase === "ready" ? review.snapshot : null;
  return (
    <div
      role="status"
      aria-label={uiText("Current preset values")}
      className="col-span-full space-y-2 rounded-md border p-3 text-sm"
    >
      <p>{uiText("This preset needs review before another save. Your draft is preserved.")}</p>
      {replaced && (
        <p>{uiText("Preset history changed. Use current values before starting another edit.")}</p>
      )}
      {row && (
        <dl className="grid grid-cols-2 gap-2">
          <dt>{uiText("Name")}</dt>
          <dd>{row.name}</dd>
          {"material_type" in row ? (
            <>
              <dt>{uiText("Material")}</dt>
              <dd>{row.material_type}</dd>
              <dt>{uiText("Brand")}</dt>
              <dd>{row.material_brand}</dd>
              <dt>{uiText("Cost per kg")}</dt>
              <dd>{row.cost_per_kg}</dd>
              {row.spoolman_filament_id !== null && (
                <>
                  <dt>{uiText("Spoolman")}</dt>
                  <dd>{uiText("Synced")}</dd>
                </>
              )}
            </>
          ) : (
            <>
              <dt>{uiText("Printer model")}</dt>
              <dd>{row.printer_model}</dd>
              <dt>{uiText("Nozzle")}</dt>
              <dd>{row.nozzle_diameter_mm}</dd>
            </>
          )}
          <dt>{uiText("Notes")}</dt>
          <dd>{row.notes}</dd>
        </dl>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          size="xs"
          variant="outline"
          onClick={onReview}
          disabled={busy || review.phase === "loading"}
        >
          {uiText("Review current values")}
        </Button>
        {row && (
          <>
            <Button size="xs" variant="outline" onClick={onAdopt} disabled={busy}>
              {uiText("Use current values")}
            </Button>
            <Button size="xs" onClick={onSave} disabled={busy || !canSave}>
              {uiText("Save revised changes")}
            </Button>
          </>
        )}
      </div>
    </div>
  );
}

function ProfileRow({
  name,
  summary,
  editor,
  onBlur,
  onClose,
  onSelect,
  onDelete,
  deleteLabel,
  editTitle,
  selected,
  detailsHost,
  children,
}: {
  name: string;
  summary: string;
  editor: React.ReactNode;
  onBlur: React.FocusEventHandler<HTMLDivElement>;
  onClose: () => void;
  onSelect: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  editTitle: string;
  selected: boolean;
  detailsHost: HTMLDivElement | null;
  children: React.ReactNode;
}) {
  const [editing, setEditing] = useState(false);
  const detailsRef = useRef<HTMLDivElement>(null);
  function toggleDetails() {
    if (!selected) onSelect();
  }
  const details = selected ? (
    <div ref={detailsRef} className="min-w-0 space-y-4 px-4 pb-4 sm:px-5">
      <div className="absolute right-5 top-4 flex items-center gap-2">
        <Button
          variant="ghost"
          size="xs"
          aria-label={uiText(editing ? "profiles.closeEditor" : "profiles.editProfile", { name })}
          aria-expanded={editing}
          onClick={() => {
            if (editing) onClose();
            setEditing(!editing);
          }}
        >
          {editing ? (
            <X className="h-3.5 w-3.5" aria-hidden />
          ) : (
            <Pencil className="h-3.5 w-3.5" aria-hidden />
          )}
          {uiText(editing ? "profiles.closeEditorLabel" : "Edit")}
        </Button>
        {onDelete && (
          <Button variant="ghost" size="xs" onClick={onDelete} aria-label={deleteLabel}>
            {uiText("Delete")}
          </Button>
        )}
      </div>
      <Modal
        open={editing}
        onClose={() => {
          onClose();
          setEditing(false);
        }}
        title={editTitle}
      >
        <div className="grid min-w-0 gap-3 sm:grid-cols-2 lg:grid-cols-6">
          <p className="col-span-full text-xs text-muted-foreground">
            {uiText("Changes save automatically when leaving a row.")}
          </p>
          {editor}
        </div>
      </Modal>
      {children}
    </div>
  ) : null;
  return (
    <div
      className="group min-w-0"
      onBlur={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          detailsRef.current?.contains(event.relatedTarget)
        )
          return;
        onBlur(event);
      }}
      role="region"
      aria-label={uiText("profiles.profileRow", { name })}
    >
      <div className={`m-3 ${selected ? "bg-muted" : ""}`}>
        <button
          type="button"
          onClick={toggleDetails}
          aria-pressed={selected}
          className="w-full min-w-0 px-3 py-3 text-left"
        >
          <h3 className="break-words text-sm font-semibold">{name}</h3>
          {summary && <p className="mt-1 break-words text-xs text-muted-foreground">{summary}</p>}
        </button>
      </div>
      {details && detailsHost && createPortal(details, detailsHost)}
    </div>
  );
}

function ProfileSummary({ name, facts }: { name: string; facts: [string, string | null][] }) {
  return (
    <div className="space-y-4 p-4 sm:p-5">
      <strong className="block break-words pr-36 text-base">{name}</strong>
      <dl className="grid gap-4 border-t border-border pt-4 sm:grid-cols-2 xl:grid-cols-4">
        {facts.map(([label, value]) => (
          <div key={label} className="min-w-0">
            <dt className="text-xs text-muted-foreground">{label}</dt>
            <dd className="mt-1 whitespace-pre-wrap break-words text-sm">{value || "—"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function FilamentProfilesCard() {
  useUiLocale();
  const helpId = useId();
  const auth = useRequireAuth();
  const { locale, t } = useI18n();
  const { user } = useAuth();
  const [activeTab, setActiveTab] = useState<"filaments" | "printers">("filaments");
  const filamentQuery = useQuery({ ...filamentProfilesOptions(), enabled: auth.isAuthenticated });
  const printerQuery = useQuery({ ...printerProfilesOptions(), enabled: auth.isAuthenticated });
  const commands = useProfileCommands();
  const filamentDenied =
    filamentQuery.error instanceof ApiError && [401, 403, 404].includes(filamentQuery.error.status);
  const printerDenied =
    printerQuery.error instanceof ApiError && [401, 403, 404].includes(printerQuery.error.status);
  const filaments = filamentDenied ? [] : (filamentQuery.data ?? []);
  const printers = printerDenied ? [] : (printerQuery.data ?? []);
  const [detailsHost, setDetailsHost] = useState<HTMLDivElement | null>(null);
  const [selectedFilamentId, setSelectedFilamentId] = useState<number | null>(null);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(null);
  const selectedFilament =
    filaments.find((profile) => profile.id === selectedFilamentId) ?? filaments[0];
  const selectedPrinter =
    printers.find((profile) => profile.id === selectedPrinterId) ?? printers[0];
  const activeDenied = activeTab === "filaments" ? filamentDenied : printerDenied;
  const activeQuery = activeTab === "filaments" ? filamentQuery : printerQuery;
  const loading = activeQuery.isPending;
  const [filamentEdits, setFilamentEdits] = useState<Record<number, FilamentDraft>>({});
  const [printerEdits, setPrinterEdits] = useState<Record<number, PrinterDraft>>({});
  const [filamentReviews, setFilamentReviews] = useState<
    Record<number, PresetReview<FilamentProfileRead>>
  >({});
  const [printerReviews, setPrinterReviews] = useState<
    Record<number, PresetReview<PrinterProfileRead>>
  >({});
  const [error, setError] = useState<string | null>(null);
  // Per-row auto-save indicator, keyed "f{id}" / "p{id}".
  const [rowStatus, setRowStatus] = useState<Record<string, "saving" | "saved">>({});
  const savedFeedbackTimers = useRef(new Map<string, ReturnType<typeof setTimeout>>());
  const mounted = useRef(false);

  useEffect(() => {
    mounted.current = true;
    const timers = savedFeedbackTimers.current;
    return () => {
      mounted.current = false;
      for (const timer of timers.values()) clearTimeout(timer);
      timers.clear();
    };
  }, []);

  const [filamentValidationErrors, setFilamentValidationErrors] = useState<Record<number, string>>(
    {},
  );
  const [deleteTarget, setDeleteTarget] = useState<DeletePresetTarget | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);

  // Spoolman sync: when enabled, presets can be imported/refreshed from Spoolman
  // (the source of truth). Synced presets are read-only here.
  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const [syncing, setSyncing] = useState(false);

  const newPresetButton = useRef<HTMLButtonElement>(null);

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
  const config = useVaultConfig({ enabled: !!user?.is_superuser && showAddFilament, retry: false });
  const currency = user?.is_superuser && !config.isError ? (config.data?.currency ?? null) : null;
  let currencyName = currency;
  if (currency) {
    try {
      currencyName = new Intl.DisplayNames(locale, { type: "currency" }).of(currency) ?? currency;
    } catch {
      currencyName = currency;
    }
  }

  function resetNewFilament() {
    setNewName("");
    setNewType("");
    setNewBrand("");
    setNewCost("");
    setNewNotes("");
    setShowAddFilament(false);
  }

  function resetNewPrinter() {
    setNewPrinterName("");
    setNewPrinterModel("");
    setNewPrinterNozzle("");
    setNewPrinterNotes("");
    setShowAddPrinter(false);
  }

  function updateFilamentEdit(id: number, patch: Partial<FilamentEdit>) {
    const profile = filaments.find((row) => row.id === id);
    if (!profile) throw new Error("Filament draft requires a loaded profile");
    setFilamentEdits((cur) => ({
      ...cur,
      [id]: {
        ...(cur[id] ?? { ...filamentEdit(profile), base: profile }),
        ...patch,
      },
    }));
    setFilamentValidationErrors((current) => {
      if (!(id in current)) return current;
      const next = { ...current };
      delete next[id];
      return next;
    });
  }

  function updatePrinterEdit(id: number, patch: Partial<PrinterEdit>) {
    const profile = printers.find((row) => row.id === id);
    if (!profile) throw new Error("Printer draft requires a loaded profile");
    setPrinterEdits((cur) => ({
      ...cur,
      [id]: { ...(cur[id] ?? { ...printerEdit(profile), base: profile }), ...patch },
    }));
  }

  async function handleCreateFilament() {
    const trimmedName = newName.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const parsedCost = parseOptionalNumber(newCost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) {
      toast.error(uiText("Invalid filament cost"));
      return;
    }
    const session = getSessionVersion();
    try {
      await commands.createFilament.mutateAsync({
        session,
        payload: {
          name: trimmedName,
          material_type: newType.trim() || null,
          material_brand: newBrand.trim() || null,
          cost_per_kg: parsedCost,
          notes: newNotes.trim() || null,
        },
      });
      if (getSessionVersion() !== session) return;
      setError(null);
      resetNewFilament();
      toast.success(uiText('Filament preset "{value1}" saved', { value1: String(trimmedName) }));
    } catch (e) {
      if (getSessionVersion() !== session) return;
      setError(userMessage(e));
      toast.error(e);
    }
  }

  function cancelSavedFeedback(key: string) {
    const timer = savedFeedbackTimers.current.get(key);
    if (timer !== undefined) {
      clearTimeout(timer);
      savedFeedbackTimers.current.delete(key);
    }
  }

  function flashSaved(key: string) {
    if (!mounted.current) return;
    cancelSavedFeedback(key);
    setRowStatus((s) => ({ ...s, [key]: "saved" }));
    const timer = setTimeout(() => {
      savedFeedbackTimers.current.delete(key);
      setRowStatus((s) => {
        // An earlier success indicator must not unlock a later pending save.
        if (s[key] !== "saved") return s;
        const next = { ...s };
        delete next[key];
        return next;
      });
    }, 1500);
    savedFeedbackTimers.current.set(key, timer);
  }

  function clearStatus(key: string) {
    cancelSavedFeedback(key);
    setRowStatus((s) => {
      const n = { ...s };
      delete n[key];
      return n;
    });
  }

  // Save when focus leaves the row entirely (not when tabbing between its fields).
  function handleRowBlur(e: React.FocusEvent<HTMLDivElement>, save: () => void) {
    if (!e.currentTarget.contains(e.relatedTarget)) save();
  }

  async function handleSyncSpoolman() {
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setSyncing(true);
    const session = getSessionVersion();
    try {
      const r = await commands.syncSpoolman.mutateAsync(session);
      if (getSessionVersion() !== session) return;
      setError(null);
      toast.success(
        uiText("Synced from Spoolman — {value1} added, {value2} updated", {
          value1: String(r.created),
          value2: String(r.updated + r.adopted),
        }),
      );
    } catch (e) {
      if (getSessionVersion() !== session) return;
      setError(userMessage(e));
      toast.error(e);
    } finally {
      if (getSessionVersion() === session) setSyncing(false);
    }
  }

  async function reviewFilament(id: number) {
    const session = getSessionVersion();
    setFilamentReviews((current) => ({ ...current, [id]: { phase: "loading" } }));
    try {
      const snapshot = await commands.reviewFilament(id, session);
      if (getSessionVersion() !== session) return;
      setFilamentReviews((current) => ({ ...current, [id]: { phase: "ready", snapshot } }));
      setError(null);
    } catch (error) {
      if (getSessionVersion() !== session) return;
      setFilamentReviews((current) => ({ ...current, [id]: { phase: "required" } }));
      setError(userMessage(error));
    }
  }

  function adoptFilament(id: number) {
    const review = filamentReviews[id];
    if (review?.phase !== "ready") throw new Error("Review current preset values first");
    setFilamentEdits((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    setFilamentValidationErrors((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    setFilamentReviews((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    setError(null);
  }

  async function autoSaveFilament(profile: FilamentProfileRead, revised = false) {
    if (!auth.isAuthenticated || filamentDenied) return;
    // Synced presets mirror Spoolman and are read-only here.
    if (profile.spoolman_filament_id != null) return;
    const review = filamentReviews[profile.id];
    if (revised ? review?.phase !== "ready" : Boolean(review)) return;
    const original = filamentEdits[profile.id]?.base ?? profile;
    const target = revised && review?.phase === "ready" ? review.snapshot : original;
    if (target.edit_epoch !== original.edit_epoch) return;
    const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
    const initial = filamentEdit(original);
    if (
      rowStatus[`f${profile.id}`] === "saving" ||
      !filamentDirty(filamentEdits[profile.id]?.base ?? profile, edit) ||
      !edit.name.trim()
    )
      return;
    const parsedCost = parseOptionalNumber(edit.cost);
    if (parsedCost !== null && Number.isNaN(parsedCost)) {
      setFilamentValidationErrors((current) => ({
        ...current,
        [profile.id]: uiText("Cost must be 0 or more."),
      }));
      toast.error(uiText("Invalid filament cost"));
      return;
    }
    setFilamentValidationErrors((current) => {
      if (!(profile.id in current)) return current;
      const next = { ...current };
      delete next[profile.id];
      return next;
    });
    const key = `f${profile.id}`;
    cancelSavedFeedback(key);
    setRowStatus((s) => ({ ...s, [key]: "saving" }));
    const payload: FilamentProfileUpdate = {};
    if (edit.name !== initial.name) payload.name = edit.name.trim();
    if (edit.materialType !== initial.materialType)
      payload.material_type = edit.materialType.trim() || null;
    if (edit.materialBrand !== initial.materialBrand)
      payload.material_brand = edit.materialBrand.trim() || null;
    if (edit.cost !== initial.cost) payload.cost_per_kg = parsedCost;
    if (edit.notes !== initial.notes) payload.notes = edit.notes.trim() || null;
    const session = getSessionVersion();
    try {
      await commands.updateFilament.mutateAsync({
        id: profile.id,
        payload,
        session,
        base: captureEditingBase(target),
      });
      if (!mounted.current || getSessionVersion() !== session) return;
      setError(null);
      setFilamentEdits((cur) => {
        const next = { ...cur };
        delete next[profile.id];
        return next;
      });
      setFilamentReviews((current) => {
        const next = { ...current };
        delete next[profile.id];
        return next;
      });
      flashSaved(key);
    } catch (e) {
      if (!mounted.current || getSessionVersion() !== session) return;
      if (needsPresetReview(parseApiError(e)))
        setFilamentReviews((current) => ({ ...current, [profile.id]: { phase: "required" } }));
      setError(userMessage(e));
      toast.error(e);
      clearStatus(key);
    }
  }

  async function confirmDeletePreset() {
    if (!deleteTarget) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    setDeleteBusy(true);
    const session = getSessionVersion();
    try {
      if (deleteTarget.kind === "filament") {
        await commands.removeFilament.mutateAsync({ id: deleteTarget.id, session });
        if (getSessionVersion() !== session) return;
        toast.success(uiText("Filament preset removed"));
      } else {
        await commands.removePrinter.mutateAsync({ id: deleteTarget.id, session });
        if (getSessionVersion() !== session) return;
        toast.success(uiText("Printer preset removed"));
      }
      if (getSessionVersion() !== session) return;
      setError(null);
      setDeleteTarget(null);
    } catch (e) {
      if (getSessionVersion() !== session) return;
      setError(userMessage(e));
      toast.error(e);
    } finally {
      if (getSessionVersion() === session) setDeleteBusy(false);
    }
  }

  async function handleCreatePrinter() {
    const trimmedName = newPrinterName.trim();
    if (!trimmedName) return;
    if (!auth.isAuthenticated) {
      auth.showAuthRequiredToast();
      return;
    }
    const parsedNozzle = parseOptionalNumber(newPrinterNozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) {
      toast.error(uiText("Invalid nozzle diameter"));
      return;
    }
    const session = getSessionVersion();
    try {
      await commands.createPrinter.mutateAsync({
        session,
        payload: {
          name: trimmedName,
          printer_model: newPrinterModel.trim() || null,
          nozzle_diameter_mm: parsedNozzle,
          notes: newPrinterNotes.trim() || null,
        },
      });
      if (getSessionVersion() !== session) return;
      setError(null);
      resetNewPrinter();
      toast.success(uiText('Printer preset "{value1}" saved', { value1: String(trimmedName) }));
    } catch (e) {
      if (getSessionVersion() !== session) return;
      setError(userMessage(e));
      toast.error(e);
    }
  }

  async function reviewPrinter(id: number) {
    const session = getSessionVersion();
    setPrinterReviews((current) => ({ ...current, [id]: { phase: "loading" } }));
    try {
      const snapshot = await commands.reviewPrinter(id, session);
      if (getSessionVersion() !== session) return;
      setPrinterReviews((current) => ({ ...current, [id]: { phase: "ready", snapshot } }));
      setError(null);
    } catch (error) {
      if (getSessionVersion() !== session) return;
      setPrinterReviews((current) => ({ ...current, [id]: { phase: "required" } }));
      setError(userMessage(error));
    }
  }

  function adoptPrinter(id: number) {
    const review = printerReviews[id];
    if (review?.phase !== "ready") throw new Error("Review current preset values first");
    setPrinterEdits((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    setPrinterReviews((current) => {
      const next = { ...current };
      delete next[id];
      return next;
    });
    setError(null);
  }

  async function autoSavePrinter(profile: PrinterProfileRead, revised = false) {
    if (!auth.isAuthenticated || printerDenied) return;
    const review = printerReviews[profile.id];
    if (revised ? review?.phase !== "ready" : Boolean(review)) return;
    const original = printerEdits[profile.id]?.base ?? profile;
    const target = revised && review?.phase === "ready" ? review.snapshot : original;
    if (target.edit_epoch !== original.edit_epoch) return;
    const edit = printerEdits[profile.id] ?? printerEdit(profile);
    const initial = printerEdit(original);
    if (
      rowStatus[`p${profile.id}`] === "saving" ||
      !printerDirty(printerEdits[profile.id]?.base ?? profile, edit) ||
      !edit.name.trim()
    )
      return;
    const parsedNozzle = parseOptionalNumber(edit.nozzle);
    if (parsedNozzle !== null && Number.isNaN(parsedNozzle)) {
      toast.error(uiText("Invalid nozzle diameter"));
      return;
    }
    const key = `p${profile.id}`;
    cancelSavedFeedback(key);
    setRowStatus((s) => ({ ...s, [key]: "saving" }));
    const payload: PrinterProfileUpdate = {};
    if (edit.name !== initial.name) payload.name = edit.name.trim();
    if (edit.model !== initial.model) payload.printer_model = edit.model.trim() || null;
    if (edit.nozzle !== initial.nozzle) payload.nozzle_diameter_mm = parsedNozzle;
    if (edit.notes !== initial.notes) payload.notes = edit.notes.trim() || null;
    const session = getSessionVersion();
    try {
      await commands.updatePrinter.mutateAsync({
        id: profile.id,
        payload,
        session,
        base: captureEditingBase(target),
      });
      if (!mounted.current || getSessionVersion() !== session) return;
      setError(null);
      setPrinterEdits((cur) => {
        const next = { ...cur };
        delete next[profile.id];
        return next;
      });
      setPrinterReviews((current) => {
        const next = { ...current };
        delete next[profile.id];
        return next;
      });
      flashSaved(key);
    } catch (e) {
      if (!mounted.current || getSessionVersion() !== session) return;
      if (needsPresetReview(parseApiError(e)))
        setPrinterReviews((current) => ({ ...current, [profile.id]: { phase: "required" } }));
      setError(userMessage(e));
      toast.error(e);
      clearStatus(key);
    }
  }

  const tabClass =
    "flex items-center gap-1 px-0 py-3 text-sm text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

  const profileActions = (
    <div className="flex items-center justify-end gap-2">
      {activeTab === "filaments" && (
        <Button
          type="button"
          variant="outline"
          size="xs"
          onClick={handleSyncSpoolman}
          loading={syncing}
          disabled={!auth.isAuthenticated || activeDenied || !spoolmanEnabled}
        >
          {!syncing && <RefreshCw className="h-3.5 w-3.5" />}
          {translateUiText(locale, "Sync Spoolman")}
        </Button>
      )}
      <Button
        ref={newPresetButton}
        type="button"
        size="xs"
        disabled={!auth.isAuthenticated || activeDenied}
        aria-expanded={activeTab === "filaments" ? showAddFilament : showAddPrinter}
        onClick={() => {
          if (!auth.isAuthenticated) {
            auth.showAuthRequiredToast();
            return;
          }
          if (activeTab === "filaments") setShowAddFilament((value) => !value);
          else setShowAddPrinter((value) => !value);
        }}
      >
        <Plus className="h-3.5 w-3.5" />
        {translateUiText(
          locale,
          activeTab === "filaments"
            ? uiText("New filament profile")
            : uiText("New printer profile"),
        )}
      </Button>
    </div>
  );

  return (
    <Localized>
      <div className="animate-panel-in space-y-4">
        <PageHeader
          title={translateUiText(locale, "Profiles")}
          description={translateUiText(locale, "profiles.description")}
          actions={profileActions}
        />
        <ConfirmModal
          open={
            deleteTarget !== null &&
            !(deleteTarget.kind === "filament" ? filamentDenied : printerDenied)
          }
          onClose={() => {
            if (!deleteBusy) setDeleteTarget(null);
          }}
          onConfirm={confirmDeletePreset}
          busy={deleteBusy}
          title={
            deleteTarget?.kind === "printer"
              ? uiText("Delete printer preset?")
              : uiText("Delete filament preset?")
          }
          description={
            deleteTarget
              ? uiText("“{value1}” will be permanently deleted. This action cannot be undone.", {
                  value1: String(deleteTarget.name),
                })
              : uiText("This preset will be permanently deleted.")
          }
          confirmLabel={uiText("Delete preset")}
        />
        {(error || activeQuery.isError) && (
          <div
            role="alert"
            className="rounded-md border border-destructive/30 bg-destructive/5 px-4 py-3 text-sm text-destructive"
          >
            <p>{error ?? userMessage(activeQuery.error)}</p>
            {activeQuery.isError && (
              <Button variant="outline" onClick={() => void activeQuery.refetch()}>
                {uiText("Retry")}
              </Button>
            )}
          </div>
        )}

        <Card className="overflow-hidden">
          <div className="border-b border-border px-5">
            <TabBar
              tabs={[
                {
                  key: "filaments",
                  label: (
                    <>
                      {translateUiText(locale, "Filaments")}{" "}
                      <Badge
                        variant="secondary"
                        className="border-0 bg-transparent px-0 text-muted-foreground"
                      >
                        {filaments.length}
                      </Badge>
                    </>
                  ),
                },
                {
                  key: "printers",
                  label: (
                    <>
                      {translateUiText(locale, "Printers")}{" "}
                      <Badge
                        variant="secondary"
                        className="border-0 bg-transparent px-0 text-muted-foreground"
                      >
                        {printers.length}
                      </Badge>
                    </>
                  ),
                },
              ]}
              active={activeTab}
              onChange={setActiveTab}
              className="max-w-full flex-wrap gap-6"
              tabClassName={tabClass}
              activeTabClassName="font-semibold text-foreground"
            />
          </div>
          {activeDenied ? null : activeTab === "filaments" ? (
            <section aria-labelledby="filament-presets-heading">
              <h2 id="filament-presets-heading" className="sr-only">
                {uiText("Filament presets")}
              </h2>

              <Modal
                open={showAddFilament}
                onClose={() => {
                  if (!commands.createFilament.isPending) setShowAddFilament(false);
                }}
                title={uiText("New filament preset")}
                description={uiText("Material information used by your saved Revisions.")}
              >
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleCreateFilament();
                  }}
                  aria-label={uiText("Create filament preset")}
                  className="space-y-4"
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <h3 className="col-span-full text-sm font-semibold">
                      {uiText("Profile name")}
                    </h3>
                    <div className="col-span-full">
                      <label>
                        <FieldLabel>{uiText("Name")}</FieldLabel>
                        <Input
                          value={newName}
                          onChange={(e) => setNewName(e.target.value)}
                          placeholder={uiText("Everyday PLA")}
                          className={compactInputClass}
                          autoFocus
                          required
                          maxLength={128}
                          aria-describedby={`${helpId}-filament-name`}
                        />
                      </label>
                      <span
                        id={`${helpId}-filament-name`}
                        className="mt-1 block text-xs text-muted-foreground"
                      >
                        {uiText("Required · up to 128 characters.")}
                      </span>
                    </div>
                    <h3 className="col-span-full border-t border-border pt-4 text-sm font-semibold">
                      {uiText("Material & cost")}
                    </h3>
                    <label>
                      <FieldLabel>{uiText("Material")}</FieldLabel>
                      <Input
                        value={newType}
                        onChange={(e) => setNewType(e.target.value)}
                        placeholder={uiText("PLA, PETG…")}
                        className={compactInputClass}
                      />
                    </label>
                    <label>
                      <FieldLabel>{uiText("Brand")}</FieldLabel>
                      <Input
                        value={newBrand}
                        onChange={(e) => setNewBrand(e.target.value)}
                        placeholder={uiText("Manufacturer")}
                        className={compactInputClass}
                      />
                    </label>
                    <div className="col-span-full">
                      <label>
                        <FieldLabel>
                          {currency
                            ? t("Cost per kg ({currency})", { currency })
                            : uiText("Cost per kg")}
                        </FieldLabel>
                        <Input
                          value={newCost}
                          onChange={(e) => setNewCost(e.target.value)}
                          inputMode="decimal"
                          placeholder="0.00"
                          className={`${compactInputClass} max-w-40`}
                          aria-describedby={`${helpId}-filament-cost`}
                        />
                      </label>
                      <p
                        id={`${helpId}-filament-cost`}
                        className="mt-1 text-xs text-muted-foreground"
                      >
                        {currencyName
                          ? t("{currency} per kilogram. Enter zero or a positive amount.", {
                              currency: currencyName,
                            })
                          : t("Enter zero or a positive amount.")}
                      </p>
                    </div>
                    <h3 className="col-span-full border-t border-border pt-4 text-sm font-semibold">
                      {uiText("Notes")}
                    </h3>
                    <div className="col-span-full">
                      <label>
                        <FieldLabel>{uiText("Notes")}</FieldLabel>
                        <textarea
                          value={newNotes}
                          onChange={(e) => setNewNotes(e.target.value)}
                          placeholder={uiText("Optional")}
                          className="min-h-20 w-full resize-y border border-input bg-card px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-describedby={`${helpId}-filament-notes`}
                        />
                      </label>
                      <span
                        id={`${helpId}-filament-notes`}
                        className="mt-1 block text-xs text-muted-foreground"
                      >
                        {uiText("Optional details to keep with this preset.")}
                      </span>
                    </div>
                  </div>
                  <div className="-mx-6 -mb-6 mt-6 flex justify-end gap-2 border-t border-border bg-plate px-6 py-4">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        resetNewFilament();
                        newPresetButton.current?.focus();
                      }}
                    >
                      {uiText("Cancel")}
                    </Button>
                    <Button
                      type="submit"
                      loading={commands.createFilament.isPending}
                      disabled={!newName.trim()}
                    >
                      {uiText("Add profile")}
                    </Button>
                  </div>
                </form>
              </Modal>

              {loading ? (
                <div className="space-y-2 p-5">
                  {[1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-12" />
                  ))}
                </div>
              ) : filamentQuery.isError && !filamentQuery.data ? null : filaments.length === 0 ? (
                <EmptyState
                  icon={Layers}
                  title={uiText("No filament presets")}
                  description={uiText("Create one to track materials, brands, and costs.")}
                  className="py-12"
                />
              ) : (
                <div className="grid min-w-0 md:grid-cols-[minmax(220px,27%)_minmax(0,1fr)]">
                  <div className="border-b border-border md:border-b-0 md:border-r">
                    {filaments.map((profile) => {
                      const review = filamentReviews[profile.id];
                      const edit = filamentEdits[profile.id] ?? filamentEdit(profile);
                      const linked = profile.spoolman_filament_id != null;
                      const locked =
                        !auth.isAuthenticated || linked || rowStatus[`f${profile.id}`] === "saving";
                      return (
                        <ProfileRow
                          key={`filament/${profile.id}`}
                          name={profile.name}
                          summary={[
                            profile.material_type,
                            profile.material_brand,
                            profile.cost_per_kg == null
                              ? null
                              : uiText("profiles.costSummary", {
                                  cost: formatCost(profile.cost_per_kg),
                                }),
                            Number.isFinite(profile.usage_count)
                              ? uiText("counts.usedByFiles", { count: profile.usage_count })
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          onBlur={(e) => handleRowBlur(e, () => autoSaveFilament(profile))}
                          onClose={() => void autoSaveFilament(profile)}
                          onSelect={() => setSelectedFilamentId(profile.id)}
                          onDelete={
                            !linked &&
                            auth.isAuthenticated &&
                            rowStatus[`f${profile.id}`] !== "saving"
                              ? () =>
                                  setDeleteTarget({
                                    kind: "filament",
                                    id: profile.id,
                                    name: edit.name,
                                  })
                              : undefined
                          }
                          deleteLabel={uiText("Delete filament preset {value1}", {
                            value1: edit.name,
                          })}
                          editTitle={uiText("Edit filament preset")}
                          selected={profile.id === selectedFilament?.id}
                          detailsHost={detailsHost}
                          editor={
                            <>
                              <label className="min-w-0 sm:col-span-2">
                                <RowLabel>{uiText("Name")}</RowLabel>
                                <Input
                                  value={edit.name}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, { name: e.target.value })
                                  }
                                  disabled={locked}
                                  aria-label={uiText("Filament preset name {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  className={compactInputClass}
                                />
                              </label>
                              <label>
                                <RowLabel>{uiText("Material")}</RowLabel>
                                <Input
                                  value={edit.materialType}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, { materialType: e.target.value })
                                  }
                                  disabled={locked}
                                  aria-label={uiText("Filament type {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder="PLA"
                                  className={compactInputClass}
                                />
                              </label>
                              <label>
                                <RowLabel>{uiText("Brand")}</RowLabel>
                                <Input
                                  value={edit.materialBrand}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, {
                                      materialBrand: e.target.value,
                                    })
                                  }
                                  disabled={locked}
                                  aria-label={uiText("Filament brand {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder={uiText("Brand")}
                                  className={compactInputClass}
                                />
                              </label>
                              <label className="lg:col-span-2">
                                <RowLabel>{uiText("Cost / kg")}</RowLabel>
                                <Input
                                  value={edit.cost}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, { cost: e.target.value })
                                  }
                                  disabled={locked}
                                  inputMode="decimal"
                                  aria-label={uiText("Filament cost per kg {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  aria-invalid={Boolean(filamentValidationErrors[profile.id])}
                                  aria-describedby={
                                    filamentValidationErrors[profile.id]
                                      ? `filament-cost-error-${profile.id}`
                                      : undefined
                                  }
                                  placeholder="0.00"
                                  className={compactInputClass}
                                />
                                {filamentValidationErrors[profile.id] && (
                                  <span
                                    id={`filament-cost-error-${profile.id}`}
                                    role="alert"
                                    className="mt-1 block text-xs text-destructive"
                                  >
                                    {filamentValidationErrors[profile.id]}
                                  </span>
                                )}
                              </label>
                              <label className="col-span-full min-w-0">
                                <RowLabel>{uiText("Notes")}</RowLabel>
                                <textarea
                                  value={edit.notes}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, { notes: e.target.value })
                                  }
                                  disabled={locked}
                                  aria-label={uiText("Filament notes {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder={uiText("Optional")}
                                  className="min-h-16 w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-disabled disabled:opacity-50"
                                />
                              </label>
                              <div className="col-span-full flex items-center gap-2">
                                {linked ? (
                                  <Badge variant="success">{uiText("Synced")}</Badge>
                                ) : (
                                  <>
                                    <RowStatus state={rowStatus[`f${profile.id}`]} />
                                    <Button
                                      type="button"
                                      variant="ghost"
                                      size="xs"
                                      onClick={() =>
                                        setDeleteTarget({
                                          kind: "filament",
                                          id: profile.id,
                                          name: edit.name,
                                        })
                                      }
                                      disabled={
                                        !auth.isAuthenticated ||
                                        rowStatus[`f${profile.id}`] === "saving"
                                      }
                                      aria-label={uiText("Delete filament preset {value1}", {
                                        value1: String(edit.name),
                                      })}
                                      title={uiText("Delete")}
                                      className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                    >
                                      <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                      {uiText("Delete")}
                                    </Button>
                                  </>
                                )}
                              </div>
                            </>
                          }
                        >
                          <ProfileSettingsDetails
                            kind="filament"
                            profile={profile}
                            initiallyExpanded
                          />
                          {review && (
                            <PresetReviewPanel
                              review={review}
                              replaced={
                                review.phase === "ready" &&
                                review.snapshot.edit_epoch !==
                                  (filamentEdits[profile.id]?.base ?? profile).edit_epoch
                              }
                              busy={rowStatus[`f${profile.id}`] === "saving"}
                              canSave={
                                !linked &&
                                Boolean(edit.name.trim()) &&
                                review.phase === "ready" &&
                                review.snapshot.edit_epoch ===
                                  (filamentEdits[profile.id]?.base ?? profile).edit_epoch
                              }
                              onReview={() => void reviewFilament(profile.id)}
                              onAdopt={() => adoptFilament(profile.id)}
                              onSave={() => void autoSaveFilament(profile, true)}
                            />
                          )}
                        </ProfileRow>
                      );
                    })}
                  </div>
                  <aside className="relative min-w-0">
                    {selectedFilament && (
                      <ProfileSummary
                        name={selectedFilament.name}
                        facts={[
                          [uiText("Material"), selectedFilament.material_type],
                          [uiText("Brand"), selectedFilament.material_brand],
                          [
                            uiText("Cost per kg"),
                            selectedFilament.cost_per_kg === null
                              ? null
                              : formatCost(selectedFilament.cost_per_kg),
                          ],
                          [uiText("Notes"), selectedFilament.notes],
                        ]}
                      />
                    )}
                    <div ref={setDetailsHost} />
                  </aside>
                </div>
              )}
            </section>
          ) : (
            <section aria-labelledby="printer-presets-heading">
              <h2 id="printer-presets-heading" className="sr-only">
                {uiText("Printer presets")}
              </h2>

              <Modal
                open={showAddPrinter}
                onClose={() => {
                  if (!commands.createPrinter.isPending) setShowAddPrinter(false);
                }}
                title={uiText("New printer preset")}
                description={uiText(
                  "Describe the settings captured with a Revision; this does not change a connected printer.",
                )}
              >
                <form
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleCreatePrinter();
                  }}
                  aria-label={uiText("Create printer preset")}
                  className="space-y-4"
                >
                  <div className="grid gap-3 sm:grid-cols-2">
                    <h3 className="col-span-full text-sm font-semibold">
                      {uiText("Profile name")}
                    </h3>
                    <div className="col-span-full">
                      <label>
                        <FieldLabel>{uiText("Name")}</FieldLabel>
                        <Input
                          value={newPrinterName}
                          onChange={(e) => setNewPrinterName(e.target.value)}
                          placeholder={uiText("Voron 2.4 — 0.4 mm")}
                          className={compactInputClass}
                          autoFocus
                          required
                          maxLength={128}
                          aria-describedby={`${helpId}-printer-name`}
                        />
                      </label>
                      <span
                        id={`${helpId}-printer-name`}
                        className="mt-1 block text-xs text-muted-foreground"
                      >
                        {uiText("Required · up to 128 characters.")}
                      </span>
                    </div>
                    <h3 className="col-span-full border-t border-border pt-4 text-sm font-semibold">
                      {uiText("Printer & nozzle")}
                    </h3>
                    <label>
                      <FieldLabel>{uiText("Printer model")}</FieldLabel>
                      <Input
                        value={newPrinterModel}
                        onChange={(e) => setNewPrinterModel(e.target.value)}
                        placeholder={uiText("Machine model")}
                        className={compactInputClass}
                      />
                    </label>
                    <label>
                      <FieldLabel>{uiText("Nozzle diameter")}</FieldLabel>
                      <Input
                        value={newPrinterNozzle}
                        onChange={(e) => setNewPrinterNozzle(e.target.value)}
                        inputMode="decimal"
                        placeholder="0.4"
                        className={compactInputClass}
                      />
                    </label>
                    <h3 className="col-span-full border-t border-border pt-4 text-sm font-semibold">
                      {uiText("Notes")}
                    </h3>
                    <div className="col-span-full">
                      <label>
                        <FieldLabel>{uiText("Notes")}</FieldLabel>
                        <textarea
                          value={newPrinterNotes}
                          onChange={(e) => setNewPrinterNotes(e.target.value)}
                          placeholder={uiText("Optional")}
                          className="min-h-20 w-full resize-y border border-input bg-card px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-describedby={`${helpId}-printer-notes`}
                        />
                      </label>
                      <span
                        id={`${helpId}-printer-notes`}
                        className="mt-1 block text-xs text-muted-foreground"
                      >
                        {uiText("Optional details to keep with this preset.")}
                      </span>
                    </div>
                  </div>
                  <div className="-mx-6 -mb-6 mt-6 flex justify-end gap-2 border-t border-border bg-plate px-6 py-4">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => {
                        resetNewPrinter();
                        newPresetButton.current?.focus();
                      }}
                    >
                      {uiText("Cancel")}
                    </Button>
                    <Button
                      type="submit"
                      loading={commands.createPrinter.isPending}
                      disabled={!newPrinterName.trim()}
                    >
                      {uiText("Add profile")}
                    </Button>
                  </div>
                </form>
              </Modal>

              {loading ? (
                <div className="space-y-2 p-5">
                  {[1, 2, 3].map((i) => (
                    <Skeleton key={i} className="h-12" />
                  ))}
                </div>
              ) : printerQuery.isError && !printerQuery.data ? null : printers.length === 0 ? (
                <EmptyState
                  icon={Printer}
                  title={uiText("No printer presets")}
                  description={uiText("Create one to reuse machine and nozzle settings.")}
                  className="py-12"
                />
              ) : (
                <div className="grid min-w-0 md:grid-cols-[minmax(220px,27%)_minmax(0,1fr)]">
                  <div className="border-b border-border md:border-b-0 md:border-r">
                    {printers.map((profile) => {
                      const review = printerReviews[profile.id];
                      const edit = printerEdits[profile.id] ?? printerEdit(profile);
                      return (
                        <ProfileRow
                          key={`printer/${profile.id}`}
                          name={profile.name}
                          summary={[
                            profile.printer_model,
                            profile.nozzle_diameter_mm == null
                              ? null
                              : uiText("profiles.nozzleSummary", {
                                  nozzle: formatNozzle(profile.nozzle_diameter_mm),
                                }),
                            Number.isFinite(profile.usage_count)
                              ? uiText("counts.usedByFiles", { count: profile.usage_count })
                              : null,
                          ]
                            .filter(Boolean)
                            .join(" · ")}
                          onBlur={(e) => handleRowBlur(e, () => autoSavePrinter(profile))}
                          onClose={() => void autoSavePrinter(profile)}
                          onSelect={() => setSelectedPrinterId(profile.id)}
                          onDelete={
                            auth.isAuthenticated && rowStatus[`p${profile.id}`] !== "saving"
                              ? () =>
                                  setDeleteTarget({
                                    kind: "printer",
                                    id: profile.id,
                                    name: edit.name,
                                  })
                              : undefined
                          }
                          deleteLabel={uiText("Delete printer preset {value1}", {
                            value1: edit.name,
                          })}
                          editTitle={uiText("Edit printer preset")}
                          selected={profile.id === selectedPrinter?.id}
                          detailsHost={detailsHost}
                          editor={
                            <>
                              <label className="min-w-0 sm:col-span-2">
                                <RowLabel>{uiText("Name")}</RowLabel>
                                <Input
                                  value={edit.name}
                                  onChange={(e) =>
                                    updatePrinterEdit(profile.id, { name: e.target.value })
                                  }
                                  disabled={
                                    !auth.isAuthenticated ||
                                    rowStatus[`p${profile.id}`] === "saving"
                                  }
                                  aria-label={uiText("Printer preset name {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  className={compactInputClass}
                                />
                              </label>
                              <label className="lg:col-span-2">
                                <RowLabel>{uiText("Printer model")}</RowLabel>
                                <Input
                                  value={edit.model}
                                  onChange={(e) =>
                                    updatePrinterEdit(profile.id, { model: e.target.value })
                                  }
                                  disabled={
                                    !auth.isAuthenticated ||
                                    rowStatus[`p${profile.id}`] === "saving"
                                  }
                                  aria-label={uiText("Printer model {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder={uiText("Machine model")}
                                  className={compactInputClass}
                                />
                              </label>
                              <label className="lg:col-span-2">
                                <RowLabel>{uiText("Nozzle")}</RowLabel>
                                <Input
                                  value={edit.nozzle}
                                  onChange={(e) =>
                                    updatePrinterEdit(profile.id, { nozzle: e.target.value })
                                  }
                                  disabled={
                                    !auth.isAuthenticated ||
                                    rowStatus[`p${profile.id}`] === "saving"
                                  }
                                  inputMode="decimal"
                                  aria-label={uiText("Printer nozzle diameter {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder="0.4"
                                  className={compactInputClass}
                                />
                              </label>
                              <label className="col-span-full min-w-0">
                                <RowLabel>{uiText("Notes")}</RowLabel>
                                <textarea
                                  value={edit.notes}
                                  onChange={(e) =>
                                    updatePrinterEdit(profile.id, { notes: e.target.value })
                                  }
                                  disabled={
                                    !auth.isAuthenticated ||
                                    rowStatus[`p${profile.id}`] === "saving"
                                  }
                                  aria-label={uiText("Printer notes {value1}", {
                                    value1: String(profile.id),
                                  })}
                                  placeholder={uiText("Optional")}
                                  className="min-h-16 w-full resize-y rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-disabled disabled:opacity-50"
                                />
                              </label>
                              <div className="col-span-full flex items-center gap-2">
                                <RowStatus state={rowStatus[`p${profile.id}`]} />
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="xs"
                                  onClick={() =>
                                    setDeleteTarget({
                                      kind: "printer",
                                      id: profile.id,
                                      name: edit.name,
                                    })
                                  }
                                  disabled={
                                    !auth.isAuthenticated ||
                                    rowStatus[`p${profile.id}`] === "saving"
                                  }
                                  aria-label={uiText("Delete printer preset {value1}", {
                                    value1: String(edit.name),
                                  })}
                                  title={uiText("Delete")}
                                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                                >
                                  <Trash2 className="h-3.5 w-3.5" aria-hidden />
                                  {uiText("Delete")}
                                </Button>
                              </div>
                            </>
                          }
                        >
                          <ProfileSettingsDetails
                            kind="printer"
                            profile={profile}
                            initiallyExpanded
                          />
                          {review && (
                            <PresetReviewPanel
                              review={review}
                              replaced={
                                review.phase === "ready" &&
                                review.snapshot.edit_epoch !==
                                  (printerEdits[profile.id]?.base ?? profile).edit_epoch
                              }
                              busy={rowStatus[`p${profile.id}`] === "saving"}
                              canSave={
                                Boolean(edit.name.trim()) &&
                                review.phase === "ready" &&
                                review.snapshot.edit_epoch ===
                                  (printerEdits[profile.id]?.base ?? profile).edit_epoch
                              }
                              onReview={() => void reviewPrinter(profile.id)}
                              onAdopt={() => adoptPrinter(profile.id)}
                              onSave={() => void autoSavePrinter(profile, true)}
                            />
                          )}
                        </ProfileRow>
                      );
                    })}
                  </div>
                  <aside className="relative min-w-0">
                    {selectedPrinter && (
                      <ProfileSummary
                        name={selectedPrinter.name}
                        facts={[
                          [uiText("Printer model"), selectedPrinter.printer_model],
                          [
                            uiText("Nozzle"),
                            selectedPrinter.nozzle_diameter_mm === null
                              ? null
                              : `${formatNozzle(selectedPrinter.nozzle_diameter_mm)} mm`,
                          ],
                          [uiText("Notes"), selectedPrinter.notes],
                        ]}
                      />
                    )}
                    <div ref={setDetailsHost} />
                  </aside>
                </div>
              )}
            </section>
          )}
        </Card>
      </div>
    </Localized>
  );
}
