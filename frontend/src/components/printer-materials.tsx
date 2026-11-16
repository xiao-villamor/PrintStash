"use client";

import { currentLocale } from "@/lib/locale";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";

import { useId, useState } from "react";
import { createPortal } from "react-dom";
import { Modal } from "@/components/ui/modal";
import "./printer-detail-content.css";

import { usePrinterMaterials } from "@/features/printers/materials";
import { parseApiError } from "@/lib/errors";
import { useSpoolmanStatus, useSpools } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { MaterialSlotRead, PrinterMaterialStateRead, PrinterRead } from "@/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const selectClassName =
  "h-10 w-full rounded-md border border-input bg-card px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-disabled disabled:opacity-50";

/** Whether a manual feed is loaded with filament, known empty, or unreported. */
type SlotState = "loaded" | "empty" | "unknown";

type DraftSlot = {
  slot_key: string;
  label: string;
  state: SlotState;
  material_type: string;
  material_brand: string;
  color_hex: string | null;
  spool_id: number | null;
};

/** Decode the `<select>` value back into the feed state; anything else is unreported. */
function parseSlotState(value: string): SlotState {
  if (value === "loaded") return "loaded";
  if (value === "empty") return "empty";
  return "unknown";
}

function sourceLabel(slot: MaterialSlotRead): string {
  if (slot.stale) return uiText("stale · treated as unknown");
  return uiText(
    slot.confidence === "operator_set"
      ? "Operator set"
      : slot.confidence === "externally_tracked"
        ? "Tracked spool"
        : "Provider state",
  );
}

function slotStateLabel(state: SlotState): string {
  return uiText(state === "loaded" ? "Loaded" : state === "empty" ? "Empty" : "Unknown");
}

export function PrinterMaterials({
  printer,
  actionsHost,
}: {
  printer: PrinterRead;
  actionsHost?: HTMLDivElement | null;
}) {
  return <MaterialEditor key={printer.id} printer={printer} actionsHost={actionsHost} />;
}
function MaterialEditor({
  printer,
  actionsHost,
}: {
  printer: PrinterRead;
  actionsHost?: HTMLDivElement | null;
}) {
  useUiLocale();
  const nozzleId = useId();
  const owner = usePrinterMaterials(printer.id);
  const [review, setReview] = useState<
    | { phase: "idle" | "required" | "loading" }
    | { phase: "ready"; snapshot: PrinterMaterialStateRead }
  >({ phase: "idle" });
  const [state, setState] = useState<PrinterMaterialStateRead | null>(null);
  const [nozzle, setNozzle] = useState("");
  const [slots, setSlots] = useState<DraftSlot[]>([]);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState<DraftSlot | null>(null);
  const [loadedTarget, setLoadedTarget] = useState<DraftSlot | null>(null);

  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const spools = useSpools({ enabled: spoolmanEnabled }).data ?? [];

  function adopt(next: PrinterMaterialStateRead) {
    setState(next);
    const tool0 = next.tools.find((tool) => tool.tool_key === "tool0");
    setNozzle(tool0?.nozzle_diameter_mm?.toString() ?? "");
    setSlots(
      next.slots
        .filter((slot) => slot.source === "manual")
        .map((slot) => ({
          slot_key: slot.slot_key,
          label: slot.label,
          state: slot.state,
          material_type: slot.material_type ?? "",
          material_brand: slot.material_brand ?? "",
          color_hex: slot.color_hex,
          spool_id: slot.spool_id,
        })),
    );
  }

  if (!state && owner.query.data) adopt(owner.query.data);
  async function reviewCurrent() {
    setReview({ phase: "loading" });
    const result = await owner.query.refetch();
    if (!owner.current()) return;
    setReview(
      result.isError || !result.data
        ? { phase: "required" }
        : { phase: "ready", snapshot: result.data },
    );
  }
  const readFailed = owner.query.isError;
  const disabled = busy || readFailed || review.phase !== "idle" || !printer.access.can_print;
  function addManualFeed() {
    let suffix = 0;
    while (slots.some((slot) => slot.slot_key === `manual${suffix}`)) suffix += 1;
    setEditing({
      slot_key: `manual${suffix}`,
      label: uiText("Manual feed {value1}", { value1: String(suffix + 1) }),
      state: "unknown",
      material_type: "",
      material_brand: "",
      color_hex: null,
      spool_id: null,
    });
  }

  async function save(nextSlots = slots) {
    if (!state || disabled) return false;
    setBusy(true);
    try {
      const result = await owner.save({
        expected_updated_at: state.updated_at,
        tools: [
          {
            tool_key: "tool0",
            get label() {
              return uiText("Tool 0");
            },
            nozzle_diameter_mm: nozzle ? Number(nozzle) : null,
          },
        ],
        slots: nextSlots.map((slot) => {
          const spool =
            slot.spool_id == null ? undefined : spools.find((row) => row.id === slot.spool_id);
          return {
            slot_key: slot.slot_key,
            label: slot.label,
            tool_key: "tool0",
            state: slot.state,
            material_type: slot.state === "loaded" ? slot.material_type.trim() || null : null,
            material_brand: slot.state === "loaded" ? slot.material_brand.trim() || null : null,
            color_hex: slot.state === "loaded" ? slot.color_hex : null,
            spool_id: slot.spool_id,
            spool_name: spool
              ? spool.filament_name ||
                spool.name ||
                uiText("Spool {value1}", { value1: String(spool.id) })
              : null,
            spool_filament_id: spool?.filament_id ?? null,
          };
        }),
      });
      if (!owner.current()) return false;
      setState(result);
      setSlots(nextSlots);
      toast.success(uiText("Materials and tools saved"));
      return true;
    } catch (error) {
      if (!owner.current()) return false;
      const status = parseApiError(error).status;
      if ([0, 409, 412, 428].includes(status) || status >= 500) setReview({ phase: "required" });
      toast.error(error);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const denied =
    owner.retired ||
    (readFailed && [401, 403, 404].includes(parseApiError(owner.query.error).status));
  if (denied || (!state && readFailed))
    return (
      <div role="alert" className="space-y-3">
        <p>{uiText("materials.loadFailed")}</p>
        <Button onClick={() => void owner.query.refetch()}>{uiText("Retry")}</Button>
      </div>
    );
  if (!state)
    return (
      <div className="space-y-3">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
      </div>
    );

  const providerSlots = (owner.query.data ?? state).slots.filter(
    (slot) => slot.source !== "manual",
  );
  const reviewContent = (review.phase !== "idle" || readFailed) && (
    <section role="alert" className="space-y-3 rounded border border-border p-4">
      <p>{uiText("library.editConflict")}</p>
      <Button disabled={busy || review.phase === "loading"} onClick={() => void reviewCurrent()}>
        {uiText("Review current values")}
      </Button>
      {review.phase === "ready" && (
        <>
          <p>
            {uiText("library.latestVersion")}:{" "}
            {review.snapshot.tools.find((tool) => tool.tool_key === "tool0")?.nozzle_diameter_mm ??
              uiText("Unknown")}
          </p>
          <ul>
            {review.snapshot.slots
              .filter((slot) => slot.source === "manual")
              .map((slot) => (
                <li key={slot.slot_key}>
                  {slot.label}: {slot.material_type ?? uiText("Unknown")}
                </li>
              ))}
          </ul>
          <Button
            disabled={busy}
            onClick={() => {
              adopt(review.snapshot);
              setReview({ phase: "idle" });
              setEditing(null);
              setLoadedTarget(null);
            }}
          >
            {uiText("Use current values")}
          </Button>
        </>
      )}
    </section>
  );
  const actions = (
    <div className="flex flex-wrap gap-2">
      <Button variant="outline" size="sm" disabled={disabled} onClick={addManualFeed}>
        {uiText("Add feed")}
      </Button>
      <Button size="sm" onClick={() => void save()} loading={busy} disabled={disabled}>
        {uiText("Save state")}
      </Button>
    </div>
  );
  function loaded(slot: DraftSlot): DraftSlot {
    const spool = spools.find((row) => row.id === slot.spool_id);
    return {
      ...slot,
      state: "loaded",
      material_type: spool?.material ?? slot.material_type,
      material_brand: spool?.vendor_name ?? slot.material_brand,
      color_hex: spool?.color_hex
        ? `#${spool.color_hex.replace(/^#/, "").slice(0, 6).toUpperCase()}`
        : slot.color_hex,
    };
  }
  function persistSlot(slot: DraftSlot) {
    const next = slots.some((row) => row.slot_key === slot.slot_key)
      ? slots.map((row) => (row.slot_key === slot.slot_key ? slot : row))
      : [...slots, slot];
    void save(next).then((saved) => {
      if (saved) {
        setEditing(null);
        setLoadedTarget(null);
      }
    });
  }
  return (
    <div className="printer-reference-surface printer-materials-reference">
      {actionsHost ? (
        createPortal(actions, actionsHost)
      ) : (
        <div className="flex justify-end">{actions}</div>
      )}
      {!editing && !loadedTarget && reviewContent}
      <div className="printer-materials-nozzle">
        <label htmlFor={nozzleId} className="space-y-2 text-[13px] font-medium text-foreground">
          {uiText("Tool 0 nozzle diameter (mm)")}
          {state.tools.some((tool) => tool.tool_key === "tool0" && tool.source !== "manual") && (
            <span className="block text-xs font-normal leading-relaxed text-muted-foreground">
              {uiText(
                "Provider-reported nozzle is shown while fresh; saving creates the manual fallback.",
              )}
            </span>
          )}
        </label>
        <Input
          id={nozzleId}
          className="h-[34px] text-[13px]"
          type="number"
          min="0.1"
          max="5"
          step="0.01"
          value={nozzle}
          onChange={(event) => setNozzle(event.target.value)}
          disabled={disabled}
          placeholder={uiText("Unknown")}
        />
      </div>
      <div className="printer-reference-list">
        {providerSlots.map((slot) => (
          <div key={`${slot.source}-${slot.slot_key}`} className="printer-reference-row">
            <div>
              <h3>{slot.label}</h3>
              <p>
                {sourceLabel(slot)} ·{" "}
                {slot.state === "loaded"
                  ? [slot.material_brand, slot.material_type].filter(Boolean).join(" · ") ||
                    uiText("Unknown")
                  : slotStateLabel(slot.state)}
                {slot.state === "loaded" ? ` · ${slotStateLabel(slot.state)}` : ""}
                {slot.observed_at
                  ? ` · ${new Date(slot.observed_at).toLocaleString(currentLocale())}`
                  : ""}
              </p>
            </div>
          </div>
        ))}
        {slots.map((slot) => (
          <div key={slot.slot_key} className="printer-reference-row">
            <div>
              <h3>{slot.label}</h3>
              <p>
                {uiText("Manual feed")} · {slotStateLabel(slot.state)}
                {slot.material_type ? ` · ${slot.material_type}` : ""}
                {slot.material_brand ? ` · ${slot.material_brand}` : ""}
                {slot.spool_id !== null
                  ? ` · ${spools.find((spool) => spool.id === slot.spool_id)?.filament_name ?? uiText("Spool {value1}", { value1: String(slot.spool_id) })}`
                  : ""}
              </p>
            </div>
            <div className="printer-reference-actions">
              <Button
                size="xs"
                variant="outline"
                disabled={disabled}
                onClick={() => setEditing({ ...slot })}
              >
                {uiText("Edit")}
              </Button>
              {slot.state !== "loaded" && (
                <Button
                  size="xs"
                  variant="outline"
                  disabled={disabled}
                  onClick={() => setLoadedTarget(loaded(slot))}
                >
                  {uiText("Set as loaded")}
                </Button>
              )}
              <Button
                size="xs"
                variant="ghost"
                disabled={disabled}
                onClick={() =>
                  setSlots((current) => current.filter((row) => row.slot_key !== slot.slot_key))
                }
              >
                {uiText("Remove")}
              </Button>
            </div>
          </div>
        ))}
        {slots.length === 0 && providerSlots.length === 0 && (
          <p className="py-5 text-sm text-muted-foreground">
            {uiText("No manual feeds. Add one for an external or unsupported filament path.")}
          </p>
        )}
      </div>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {uiText(
          "Selecting a tracked spool does not mark it loaded; use “Set as loaded” explicitly.",
        )}
      </p>
      <Modal
        open={editing !== null}
        onClose={() => {
          if (!busy) setEditing(null);
        }}
        title={uiText(
          editing && slots.some((slot) => slot.slot_key === editing.slot_key)
            ? "Edit manual feed"
            : "Add manual feed",
        )}
        description={uiText("Set the feed state and material, then save.")}
        footer={
          <>
            <Button variant="outline" disabled={busy} onClick={() => setEditing(null)}>
              {uiText("Cancel")}
            </Button>
            <Button
              loading={busy}
              disabled={disabled || !editing?.label.trim()}
              onClick={() => {
                if (editing) persistSlot(editing);
              }}
            >
              {uiText("Save state")}
            </Button>
          </>
        }
      >
        {editing && (
          <div className="space-y-5">
            {reviewContent}
            <fieldset className="space-y-4 border-t border-border pt-4">
              <legend className="pr-3 text-sm font-semibold">{uiText("Feed state")}</legend>
              <label className="block space-y-2 text-[13px] font-medium">
                {uiText("Feed label")}
                <Input
                  value={editing.label}
                  disabled={disabled}
                  onChange={(event) => setEditing({ ...editing, label: event.target.value })}
                />
              </label>
              <label className="block space-y-2 text-[13px] font-medium">
                {uiText("State")}
                <select
                  className={selectClassName}
                  value={editing.state}
                  disabled={disabled}
                  onChange={(event) =>
                    setEditing({ ...editing, state: parseSlotState(event.target.value) })
                  }
                >
                  <option value="unknown">{uiText("Unknown")}</option>
                  <option value="empty">{uiText("Empty")}</option>
                  <option value="loaded">{uiText("Loaded")}</option>
                </select>
              </label>
            </fieldset>
            <fieldset className="space-y-4 border-t border-border pt-4">
              <legend className="pr-3 text-sm font-semibold">{uiText("Material")}</legend>
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Material type")}
                  <Input
                    value={editing.material_type}
                    disabled={disabled || editing.state !== "loaded"}
                    onChange={(event) =>
                      setEditing({ ...editing, material_type: event.target.value })
                    }
                  />
                </label>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Brand")}
                  <Input
                    value={editing.material_brand}
                    disabled={disabled || editing.state !== "loaded"}
                    onChange={(event) =>
                      setEditing({ ...editing, material_brand: event.target.value })
                    }
                  />
                </label>
                <label className="block space-y-2 text-[13px] font-medium">
                  {uiText("Color")}
                  <div className="flex h-10 items-center gap-3 border border-input bg-card px-3">
                    <input
                      type="color"
                      aria-label={uiText("Color")}
                      value={editing.color_hex ?? "#808080"}
                      disabled={disabled || editing.state !== "loaded"}
                      onChange={(event) =>
                        setEditing({ ...editing, color_hex: event.target.value.toUpperCase() })
                      }
                    />
                    <span className="font-mono text-xs">
                      {editing.color_hex ?? uiText("Unknown")}
                    </span>
                  </div>
                </label>
                {spoolmanEnabled && (
                  <label className="block space-y-2 text-[13px] font-medium">
                    {uiText("Tracked spool")}
                    <select
                      className={selectClassName}
                      value={editing.spool_id ?? ""}
                      disabled={disabled}
                      onChange={(event) =>
                        setEditing({
                          ...editing,
                          spool_id: event.target.value ? Number(event.target.value) : null,
                        })
                      }
                    >
                      <option value="">{uiText("No tracked spool")}</option>
                      {spools.map((spool) => (
                        <option key={spool.id} value={spool.id}>
                          {spool.filament_name ||
                            spool.name ||
                            uiText("Spool {value1}", { value1: String(spool.id) })}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            </fieldset>
          </div>
        )}
      </Modal>
      <Modal
        open={loadedTarget !== null}
        onClose={() => {
          if (!busy) setLoadedTarget(null);
        }}
        title={uiText("Set {value1} as loaded?", { value1: loadedTarget?.label ?? "" })}
        description={uiText(
          "This updates manually recorded material state. It does not change material physically loaded in the printer.",
        )}
        footer={
          <>
            <Button variant="outline" disabled={busy} onClick={() => setLoadedTarget(null)}>
              {uiText("Cancel")}
            </Button>
            <Button
              loading={busy}
              disabled={disabled}
              onClick={() => {
                if (loadedTarget) persistSlot(loadedTarget);
              }}
            >
              {uiText("Confirm loaded feed")}
            </Button>
          </>
        }
      >
        {reviewContent}
        <dl className="printer-reference-facts">
          <div>
            <dt>{uiText("Printer")}</dt>
            <dd>{printer.name}</dd>
          </div>
          <div>
            <dt>{uiText("Feed label")}</dt>
            <dd>{loadedTarget?.label}</dd>
          </div>
          <div>
            <dt>{uiText("Material")}</dt>
            <dd>{loadedTarget?.material_type || uiText("Unknown")}</dd>
          </div>
        </dl>
      </Modal>
    </div>
  );
}
