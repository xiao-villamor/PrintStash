"use client";

import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { formatNumber } from "@/lib/format";
import { useSearchParams } from "@/lib/navigation";
import { Link } from "@/lib/link";

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
  PrinterProfileCreate,
  FilamentProfileUpdate,
  PrinterProfileUpdate,
} from "@/types";

import { usePrinters, useSpoolmanStatus, useVaultConfig } from "@/lib/queries";
import { useAuth } from "@/lib/auth-context";
import { toast } from "@/lib/toast";
import { useRequireAuth } from "@/lib/use-require-auth";
import { Check, Loader2, Plus, RefreshCw, Trash2, X, Pencil, type LucideIcon } from "lucide-react";
import { FilamentSpool } from "@/components/ui/filament-spool";
import { Printer3D } from "@/components/ui/printer-3d";
import { FilamentSpoolPrice } from "@/components/filament-spool-price";
import {
  expenseDraft,
  expenseFields,
  expenseProblem,
  expensesChanged,
  parseExpenses,
  machineCostBreakdown,
  type ExpenseDraft,
} from "@/lib/printer-expenses";
import { PrinterExpenseFields } from "@/components/printer-expense-fields";
import { PrinterProfileFields } from "@/components/printer-profile-fields";
import { PricingTemplatesPanel } from "@/components/pricing-templates";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmModal } from "@/components/ui/confirm-modal";
import { Modal } from "@/components/ui/modal";
import { PageHeader } from "@/components/ui/page-header";
import { EmptyState } from "@/components/ui/empty-state";
import { FormField } from "@/components/ui/form-field";
import { Input, inputClasses } from "@/components/ui/input";
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
  expenses: ExpenseDraft;
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

function parseOptionalNumber(value: string): number | null {
  if (!value.trim()) return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : Number.NaN;
}

function formatCost(value: number | null): string {
  return value == null ? "" : String(value);
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
    expenses: expenseDraft(profile.expenses),
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
    expensesChanged(base.expenses, edit.expenses) ||
    base.notes !== edit.notes
  );
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
      className="col-span-full space-y-2 rounded-lg border p-3 text-sm"
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
              {row.expenses &&
                expenseFields.map(({ key, label }) => (
                  <div key={key} className="contents">
                    <dt>{label}</dt>
                    <dd>{row.expenses?.[key]}</dd>
                  </div>
                ))}
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
  feedback,
  icon: Icon,
  facts,
  rate,
  notes,
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
  editor: (focusCosts: boolean) => React.ReactNode;
  feedback: React.ReactNode;
  icon: LucideIcon;
  facts: [string, string | null][];
  rate: {
    label: string;
    value: string | null;
    canConfigure: boolean;
    breakdown?: [string, string | null][];
  };
  notes: string | null;
  onBlur: React.FocusEventHandler<HTMLDivElement>;
  onClose: () => void;
  onSelect: () => void;
  onDelete?: () => void;
  deleteLabel?: string;
  editTitle: string;
  selected: boolean;
  detailsHost: HTMLElement | null;
  children: React.ReactNode;
}) {
  const [editorMode, setEditorMode] = useState<"profile" | "costs" | null>(null);
  const editing = editorMode !== null;
  const detailsRef = useRef<HTMLDivElement>(null);
  function toggleDetails() {
    if (!selected) onSelect();
  }
  const details = selected ? (
    <div ref={detailsRef} className="min-w-0 space-y-5 p-5 sm:p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <div className="grid size-10 shrink-0 place-items-center rounded-md bg-foreground/10 text-foreground">
            <Icon className="size-5" aria-hidden="true" />
          </div>
          <div className="min-w-0">
            <h3 className="break-words text-xl font-semibold tracking-tight">{name}</h3>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            aria-label={uiText(editing ? "profiles.closeEditor" : "profiles.editProfile", { name })}
            aria-expanded={editing}
            onClick={() => {
              if (editing) onClose();
              setEditorMode(editing ? null : "profile");
            }}
          >
            {editing ? (
              <X className="size-3.5" aria-hidden />
            ) : (
              <Pencil className="size-3.5" aria-hidden />
            )}
            {uiText(editing ? "profiles.closeEditorLabel" : "Edit")}
          </Button>
          {onDelete && (
            <Button variant="ghost" size="sm" onClick={onDelete} aria-label={deleteLabel}>
              {uiText("Delete")}
            </Button>
          )}
        </div>
      </header>
      <section
        aria-label={rate.label}
        className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-border bg-muted/40 px-4 py-3"
      >
        <div className="min-w-0">
          <h4 className="text-xs font-medium text-muted-foreground">{rate.label}</h4>
          {rate.value == null ? (
            <p className="mt-1 text-sm font-semibold">{uiText("Cost not configured")}</p>
          ) : (
            <output
              aria-label={rate.label}
              className="mt-1 block text-2xl font-semibold tracking-tight tabular-nums"
            >
              {rate.value}
            </output>
          )}
        </div>
        {rate.value == null && rate.canConfigure && (
          <Button variant="outline" size="sm" onClick={() => setEditorMode("costs")}>
            {uiText("Set operating costs")}
          </Button>
        )}
        {rate.breakdown && (
          <dl
            className="grid w-full grid-cols-2 gap-3 border-t border-border pt-3 sm:grid-cols-4"
            aria-label={uiText("Hourly cost breakdown")}
          >
            {rate.breakdown.map(([label, amount]) => (
              <div key={label} className="min-w-0 space-y-1">
                <dt className="text-xs text-muted-foreground">{label}</dt>
                <dd className="break-words text-sm font-medium tabular-nums">{amount}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>
      <ProfileFacts facts={facts} notes={notes} />
      <Modal
        open={editing}
        onClose={() => {
          onClose();
          setEditorMode(null);
        }}
        title={editTitle}
      >
        <div className="grid min-w-0 gap-4 sm:grid-cols-2 lg:grid-cols-6">
          <p className="col-span-full text-xs text-muted-foreground">
            {uiText("Changes save when you close this editor.")}
          </p>
          {editor(editorMode === "costs")}
        </div>
        {feedback}
      </Modal>
      {!editing && feedback}
      {children}
    </div>
  ) : null;
  return (
    <div
      className="group min-w-0"
      onBlur={(event) => {
        if (
          event.relatedTarget instanceof Node &&
          (detailsRef.current?.contains(event.relatedTarget) ||
            event.target.closest("[data-overlay-open]")?.contains(event.relatedTarget))
        )
          return;
        onBlur(event);
      }}
      role="region"
      aria-label={uiText("profiles.profileRow", { name })}
    >
      <div
        className={
          selected
            ? "rounded-md bg-foreground/12 text-foreground"
            : "rounded-md hover:bg-foreground/6"
        }
      >
        <button
          type="button"
          onClick={toggleDetails}
          aria-pressed={selected}
          className="w-full min-w-0 rounded-md px-3 py-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <span className="block break-words text-sm font-semibold">{name}</span>
          {summary && <p className="mt-1 break-words text-xs text-muted-foreground">{summary}</p>}
        </button>
      </div>
      {details && detailsHost && createPortal(details, detailsHost)}
    </div>
  );
}

function ProfileFacts({
  facts,
  notes,
}: {
  facts: [string, string | null][];
  notes: string | null;
}) {
  const populated = facts.filter(([, value]) => value);
  if (populated.length === 0 && !notes) return null;
  return (
    <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
      {populated.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words text-sm">{value}</dd>
        </div>
      ))}
      {notes && (
        <div className="col-span-full min-w-0">
          <dt className="text-xs text-muted-foreground">{uiText("Notes")}</dt>
          <dd className="mt-1 whitespace-pre-wrap break-words text-sm">{notes}</dd>
        </div>
      )}
    </dl>
  );
}

function formatProfileRate(value: number | null, currency: string | null): string | null {
  if (value === null) return null;
  const options: Intl.NumberFormatOptions = { maximumFractionDigits: 4 };
  if (currency) {
    options.style = "currency";
    options.currency = currency;
  }
  return formatNumber(value, options);
}

export function FilamentProfilesCard() {
  useUiLocale();
  const helpId = useId();
  const filamentFormId = useId();
  const printerFormId = useId();
  const auth = useRequireAuth();
  const { locale, t } = useI18n();
  const { user } = useAuth();
  const params = useSearchParams();
  const initialTab = params.get("tab");
  const [activeTab, setActiveTab] = useState<"filaments" | "printers" | "pricing">(
    initialTab === "pricing" || initialTab === "printers" ? initialTab : "filaments",
  );
  const filamentQuery = useQuery({ ...filamentProfilesOptions(), enabled: auth.isAuthenticated });
  const printerQuery = useQuery({ ...printerProfilesOptions(), enabled: auth.isAuthenticated });
  const fleetQuery = usePrinters({ enabled: auth.isAuthenticated && activeTab === "printers" });
  const commands = useProfileCommands();
  const filamentDenied =
    filamentQuery.error instanceof ApiError && [401, 403, 404].includes(filamentQuery.error.status);
  const printerDenied =
    printerQuery.error instanceof ApiError && [401, 403, 404].includes(printerQuery.error.status);
  const filaments = filamentDenied ? [] : (filamentQuery.data ?? []);
  const printers = printerDenied ? [] : (printerQuery.data ?? []);
  const [detailsHost, setDetailsHost] = useState<HTMLElement | null>(null);
  const [selectedFilamentId, setSelectedFilamentId] = useState<number | null>(null);
  const [selectedPrinterId, setSelectedPrinterId] = useState<number | null>(() => {
    const id = Number(params.get("profile"));
    return Number.isSafeInteger(id) && id > 0 ? id : null;
  });
  const selectedFilament =
    filaments.find((profile) => profile.id === selectedFilamentId) ?? filaments[0];
  const selectedPrinter =
    printers.find((profile) => profile.id === selectedPrinterId) ?? printers[0];
  const assigned = selectedPrinter
    ? fleetQuery.data?.filter((printer) => printer.cost_profile_id === selectedPrinter.id)
    : undefined;
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
  const [newPrinterExpenses, setNewPrinterExpenses] = useState(() => expenseDraft(null));
  const config = useVaultConfig({ enabled: !!user?.is_superuser, retry: false });
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
    setNewPrinterExpenses(expenseDraft(null));
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
    const expenses = parseExpenses(newPrinterExpenses, newPrinterModel);
    const problem = expenseProblem(expenses);
    if (problem) {
      toast.error(problem);
      return;
    }
    const payload: PrinterProfileCreate = {
      name: trimmedName,
      printer_model: newPrinterModel.trim() || null,
      nozzle_diameter_mm: parsedNozzle,
      notes: newPrinterNotes.trim() || null,
    };
    if (expenses) payload.expenses = expenses;
    const session = getSessionVersion();
    try {
      const created = await commands.createPrinter.mutateAsync({ session, payload });
      if (getSessionVersion() !== session) return;
      setError(null);
      setSelectedPrinterId(created.id);
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
    const expenses = parseExpenses(edit.expenses, edit.model);
    if (expenseProblem(expenses)) return;
    const key = `p${profile.id}`;
    cancelSavedFeedback(key);
    setRowStatus((s) => ({ ...s, [key]: "saving" }));
    const payload: PrinterProfileUpdate = {};
    if (edit.name !== initial.name) payload.name = edit.name.trim();
    if (edit.model !== initial.model) payload.printer_model = edit.model.trim() || null;
    if (edit.nozzle !== initial.nozzle) payload.nozzle_diameter_mm = parsedNozzle;
    if (expensesChanged(initial.expenses, edit.expenses)) payload.expenses = expenses;
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
    "flex h-7.5 items-center gap-1.5 rounded-md px-3 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

  const profileActions = (
    <div className="flex items-center justify-end gap-2">
      {activeTab === "filaments" && (
        <Button
          type="button"
          variant="outline"
          size="sm"
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
        size="sm"
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
          activeTab === "filaments" ? uiText("New filament preset") : uiText("New printer preset"),
        )}
      </Button>
    </div>
  );

  return (
    <Localized>
      <div className="space-y-5">
        <PageHeader
          title={translateUiText(locale, "Profiles")}
          description={translateUiText(locale, "profiles.description")}
          actions={activeTab === "pricing" ? undefined : profileActions}
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
        {activeTab !== "pricing" && (error || activeQuery.isError) && (
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

        <div className="space-y-3">
          <div>
            <TabBar
              tabs={[
                {
                  key: "filaments",
                  label: (
                    <>
                      {translateUiText(locale, "Filaments")}{" "}
                      <Badge
                        variant="secondary"
                        className="rounded-full border-0 bg-foreground/10 px-1.5 font-mono text-xs text-foreground/82"
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
                        className="rounded-full border-0 bg-foreground/10 px-1.5 font-mono text-xs text-foreground/82"
                      >
                        {printers.length}
                      </Badge>
                    </>
                  ),
                },
                { key: "pricing", label: uiText("Pricing templates") },
              ]}
              active={activeTab}
              onChange={setActiveTab}
              showIndicator={false}
              className="w-fit max-w-full gap-0.5 rounded-lg border border-border bg-card p-0.75"
              tabClassName={tabClass}
              activeTabClassName="bg-foreground/12 text-foreground"
            />
          </div>
          {activeTab === "pricing" ? (
            auth.isAuthenticated && <PricingTemplatesPanel admin={user?.is_superuser === true} />
          ) : activeDenied ? null : activeTab === "filaments" ? (
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
                footer={
                  <>
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
                      form={filamentFormId}
                      loading={commands.createFilament.isPending}
                      disabled={!newName.trim()}
                    >
                      {uiText("Add preset")}
                    </Button>
                  </>
                }
              >
                <form
                  id={filamentFormId}
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleCreateFilament();
                  }}
                  aria-label={uiText("Create filament preset")}
                  className="space-y-5"
                >
                  <FormField
                    label={uiText("Name")}
                    hintId={`${helpId}-filament-name`}
                    hint={uiText("Required · up to 128 characters.")}
                  >
                    <Input
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder={uiText("Everyday PLA")}
                      data-autofocus
                      required
                      maxLength={128}
                      aria-describedby={`${helpId}-filament-name`}
                    />
                  </FormField>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <h3 className="col-span-full text-field font-semibold">
                      {uiText("Material & cost")}
                    </h3>
                    <FormField label={uiText("Material")}>
                      <Input
                        value={newType}
                        onChange={(e) => setNewType(e.target.value)}
                        placeholder={uiText("PLA, PETG…")}
                      />
                    </FormField>
                    <FormField label={uiText("Brand")}>
                      <Input
                        value={newBrand}
                        onChange={(e) => setNewBrand(e.target.value)}
                        placeholder={uiText("Manufacturer")}
                      />
                    </FormField>
                    <FormField
                      className="col-span-full"
                      label={
                        currency
                          ? t("Cost per kg ({currency})", { currency })
                          : uiText("Cost per kg")
                      }
                      hintId={`${helpId}-filament-cost`}
                      hint={
                        currencyName
                          ? t("{currency} per kilogram. Enter zero or a positive amount.", {
                              currency: currencyName,
                            })
                          : t("Enter zero or a positive amount.")
                      }
                    >
                      <Input
                        value={newCost}
                        onChange={(e) => setNewCost(e.target.value)}
                        inputMode="decimal"
                        placeholder="0.00"
                        className="max-w-40"
                        aria-describedby={`${helpId}-filament-cost`}
                      />
                    </FormField>
                    <FilamentSpoolPrice
                      currency={currency}
                      disabled={commands.createFilament.isPending}
                      onApply={setNewCost}
                    />
                  </div>
                  <FormField
                    label={uiText("Notes")}
                    hintId={`${helpId}-filament-notes`}
                    hint={uiText("Optional details to keep with this preset.")}
                  >
                    <textarea
                      value={newNotes}
                      onChange={(e) => setNewNotes(e.target.value)}
                      placeholder={uiText("Optional")}
                      className={inputClasses}
                      aria-describedby={`${helpId}-filament-notes`}
                    />
                  </FormField>
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
                  icon={FilamentSpool}
                  title={uiText("No filament presets")}
                  description={uiText("Create one to track materials, brands, and costs.")}
                  className="py-12"
                />
              ) : (
                <div className="grid min-w-0 items-start gap-3 md:grid-cols-[minmax(15rem,1fr)_minmax(0,2fr)]">
                  <div className="space-y-1 rounded-lg border border-border bg-card p-2">
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
                          icon={FilamentSpool}
                          facts={[
                            [uiText("Material"), profile.material_type],
                            [uiText("Brand"), profile.material_brand],
                          ]}
                          notes={profile.notes}
                          rate={{
                            label: uiText("Cost per kg"),
                            value: formatProfileRate(profile.cost_per_kg, currency),
                            canConfigure: false,
                          }}
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
                          editor={() => (
                            <>
                              <FormField className="min-w-0 sm:col-span-2" label={uiText("Name")}>
                                <Input
                                  value={edit.name}
                                  onChange={(e) =>
                                    updateFilamentEdit(profile.id, { name: e.target.value })
                                  }
                                  disabled={locked}
                                  aria-label={uiText("Filament preset name {value1}", {
                                    value1: String(profile.id),
                                  })}
                                />
                              </FormField>
                              <FormField label={uiText("Material")}>
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
                                />
                              </FormField>
                              <FormField label={uiText("Brand")}>
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
                                />
                              </FormField>
                              <FormField
                                className="lg:col-span-2"
                                label={
                                  currency
                                    ? `${uiText("Cost / kg")} (${currency})`
                                    : uiText("Cost / kg")
                                }
                              >
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
                              </FormField>
                              {!linked && (
                                <FilamentSpoolPrice
                                  currency={currency}
                                  disabled={locked}
                                  onApply={(cost) => updateFilamentEdit(profile.id, { cost })}
                                />
                              )}
                              <FormField className="col-span-full min-w-0" label={uiText("Notes")}>
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
                                  className={inputClasses}
                                />
                              </FormField>
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
                          )}
                          feedback={
                            review && (
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
                            )
                          }
                        >
                          <ProfileSettingsDetails kind="filament" profile={profile} />
                        </ProfileRow>
                      );
                    })}
                  </div>
                  <aside
                    className="min-w-0 rounded-lg border border-border bg-card"
                    ref={setDetailsHost}
                  />
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
                footer={
                  <>
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
                      form={printerFormId}
                      loading={commands.createPrinter.isPending}
                      disabled={!newPrinterName.trim()}
                    >
                      {uiText("Add preset")}
                    </Button>
                  </>
                }
              >
                <form
                  id={printerFormId}
                  onSubmit={(e) => {
                    e.preventDefault();
                    handleCreatePrinter();
                  }}
                  aria-label={uiText("Create printer preset")}
                  className="space-y-5"
                >
                  <FormField
                    label={uiText("Name")}
                    hintId={`${helpId}-printer-name`}
                    hint={uiText("Required · up to 128 characters.")}
                  >
                    <Input
                      value={newPrinterName}
                      onChange={(e) => setNewPrinterName(e.target.value)}
                      placeholder={uiText("Voron 2.4 — 0.4 mm")}
                      data-autofocus
                      required
                      maxLength={128}
                      aria-describedby={`${helpId}-printer-name`}
                    />
                  </FormField>
                  <div className="space-y-4">
                    <h3 className="text-field font-semibold">{uiText("Printer & nozzle")}</h3>
                    <PrinterProfileFields
                      model={newPrinterModel}
                      nozzle={newPrinterNozzle}
                      disabled={commands.createPrinter.isPending}
                      onModelChange={setNewPrinterModel}
                      onNozzleChange={setNewPrinterNozzle}
                    />
                  </div>
                  <FormField
                    label={uiText("Notes")}
                    hintId={`${helpId}-printer-notes`}
                    hint={uiText("Optional details to keep with this preset.")}
                  >
                    <textarea
                      value={newPrinterNotes}
                      onChange={(e) => setNewPrinterNotes(e.target.value)}
                      placeholder={uiText("Optional")}
                      className={inputClasses}
                      aria-describedby={`${helpId}-printer-notes`}
                    />
                  </FormField>
                  <PrinterExpenseFields
                    currency={currency}
                    draft={newPrinterExpenses}
                    model={newPrinterModel}
                    disabled={commands.createPrinter.isPending}
                    onChange={setNewPrinterExpenses}
                  />
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
                  icon={Printer3D}
                  title={uiText("No printer presets")}
                  description={uiText("Create one to reuse machine and nozzle settings.")}
                  className="py-12"
                />
              ) : (
                <div className="grid min-w-0 items-start gap-3 md:grid-cols-[minmax(15rem,1fr)_minmax(0,2fr)]">
                  <div className="space-y-1 rounded-lg border border-border bg-card p-2">
                    {printers.map((profile) => {
                      const review = printerReviews[profile.id];
                      const edit = printerEdits[profile.id] ?? printerEdit(profile);
                      const rates = profile.expenses
                        ? machineCostBreakdown(profile.expenses)
                        : null;
                      return (
                        <ProfileRow
                          key={`printer/${profile.id}`}
                          name={profile.name}
                          icon={Printer3D}
                          facts={[
                            [uiText("Printer model"), profile.printer_model],
                            [
                              uiText("Nozzle"),
                              profile.nozzle_diameter_mm === null
                                ? null
                                : `${formatNozzle(profile.nozzle_diameter_mm)} mm`,
                            ],
                          ]}
                          notes={profile.notes}
                          rate={{
                            label: uiText("Machine cost per print hour"),
                            value: formatProfileRate(
                              rates
                                ? Object.values(rates).reduce((total, rate) => total + rate, 0)
                                : null,
                              currency,
                            ),
                            breakdown: rates
                              ? [
                                  [
                                    uiText("Depreciation"),
                                    formatProfileRate(rates.depreciation, currency),
                                  ],
                                  [
                                    uiText("Maintenance"),
                                    formatProfileRate(rates.maintenance, currency),
                                  ],
                                  [
                                    uiText("Electricity"),
                                    formatProfileRate(rates.electricity, currency),
                                  ],
                                  [
                                    uiText("Other costs"),
                                    formatProfileRate(rates.additional, currency),
                                  ],
                                ]
                              : undefined,
                            canConfigure: auth.isAuthenticated,
                          }}
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
                          editor={(focusCosts) => (
                            <>
                              <FormField className="col-span-full" label={uiText("Name")}>
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
                                />
                              </FormField>
                              <PrinterProfileFields
                                id={profile.id}
                                model={edit.model}
                                nozzle={edit.nozzle}
                                disabled={
                                  !auth.isAuthenticated || rowStatus[`p${profile.id}`] === "saving"
                                }
                                onModelChange={(model) => updatePrinterEdit(profile.id, { model })}
                                onNozzleChange={(nozzle) =>
                                  updatePrinterEdit(profile.id, { nozzle })
                                }
                              />
                              <FormField className="col-span-full min-w-0" label={uiText("Notes")}>
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
                                  className={inputClasses}
                                />
                              </FormField>
                              <PrinterExpenseFields
                                currency={currency}
                                focus={focusCosts}
                                draft={edit.expenses}
                                model={edit.model}
                                disabled={
                                  !auth.isAuthenticated || rowStatus[`p${profile.id}`] === "saving"
                                }
                                onChange={(expenses) => updatePrinterEdit(profile.id, { expenses })}
                              />
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
                          )}
                          feedback={
                            review && (
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
                            )
                          }
                        >
                          {assigned && (
                            <section
                              className="space-y-2 border-t border-border pt-4"
                              aria-label={uiText("Assigned printers")}
                            >
                              <h4 className="text-sm font-semibold">
                                {uiText("Assigned printers")}
                              </h4>
                              {assigned.length > 0 ? (
                                <ul className="space-y-2 text-sm">
                                  {assigned.map((printer) => (
                                    <li key={printer.id}>
                                      <Link
                                        href={`/printers/${printer.id}?tab=settings`}
                                        className="break-words text-foreground underline underline-offset-4"
                                      >
                                        {printer.name}
                                      </Link>
                                    </li>
                                  ))}
                                </ul>
                              ) : (
                                <Button asChild size="sm" variant="outline">
                                  <Link href="/printers">{uiText("Manage printers")}</Link>
                                </Button>
                              )}
                            </section>
                          )}
                          <ProfileSettingsDetails kind="printer" profile={profile} />
                        </ProfileRow>
                      );
                    })}
                  </div>
                  <aside
                    className="min-w-0 rounded-lg border border-border bg-card"
                    ref={setDetailsHost}
                  />
                </div>
              )}
            </section>
          )}
        </div>
      </div>
    </Localized>
  );
}
