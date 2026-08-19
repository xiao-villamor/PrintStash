"use client";

import { useEffect, useState } from "react";

import { getPrinterMaterialState, updatePrinterManualMaterialState } from "@/lib/api";
import { useSpoolmanStatus, useSpools } from "@/lib/queries";
import { toast } from "@/lib/toast";
import type { MaterialSlotRead, PrinterMaterialStateRead, PrinterRead } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";

const selectClassName =
  "h-10 w-full rounded-md border border-input bg-background px-3 text-sm text-foreground ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50";

type DraftSlot = {
  slot_key: string;
  label: string;
  state: "loaded" | "empty" | "unknown";
  material_type: string;
  material_brand: string;
  color_hex: string;
  spool_id: number | null;
};

function sourceLabel(slot: MaterialSlotRead): string {
  if (slot.stale) return "stale · treated as unknown";
  return slot.confidence.replace("_", " ");
}

export function PrinterMaterials({ printer }: { printer: PrinterRead }) {
  const [state, setState] = useState<PrinterMaterialStateRead | null>(null);
  const [nozzle, setNozzle] = useState("");
  const [slots, setSlots] = useState<DraftSlot[]>([]);
  const [busy, setBusy] = useState(false);
  const spoolmanEnabled = useSpoolmanStatus().data?.enabled ?? false;
  const spools = useSpools({ enabled: spoolmanEnabled }).data ?? [];

  async function load() {
    const next = await getPrinterMaterialState(printer.id);
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
          color_hex: slot.color_hex ?? "#808080",
          spool_id: slot.spool_id,
        })),
    );
  }

  useEffect(() => {
    void load().catch(toast.error);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [printer.id]);

  function patchSlot(index: number, patch: Partial<DraftSlot>) {
    setSlots((current) => current.map((slot, slotIndex) => (slotIndex === index ? { ...slot, ...patch } : slot)));
  }

  function addManualFeed() {
    setSlots((current) => {
      let suffix = 0;
      while (current.some((slot) => slot.slot_key === `manual${suffix}`)) suffix += 1;
      return [...current, { slot_key: `manual${suffix}`, label: `Manual feed ${suffix + 1}`, state: "unknown", material_type: "", material_brand: "", color_hex: "#808080", spool_id: null }];
    });
  }

  async function save() {
    if (!state) return;
    setBusy(true);
    try {
      const result = await updatePrinterManualMaterialState(printer.id, {
        expected_updated_at: state.updated_at,
        tools: [{
          tool_key: "tool0",
          label: "Tool 0",
          nozzle_diameter_mm: nozzle ? Number(nozzle) : null,
        }],
        slots: slots.map((slot) => {
          const spool = slot.spool_id == null ? undefined : spools.find((row) => row.id === slot.spool_id);
          return {
            slot_key: slot.slot_key,
            label: slot.label,
            tool_key: "tool0",
            state: slot.state,
            material_type: slot.state === "loaded" ? slot.material_type.trim() || null : null,
            material_brand: slot.state === "loaded" ? slot.material_brand.trim() || null : null,
            color_hex: slot.state === "loaded" ? slot.color_hex : null,
            spool_id: slot.spool_id,
            spool_name: spool ? spool.filament_name || spool.name || `Spool ${spool.id}` : null,
            spool_filament_id: spool?.filament_id ?? null,
          };
        }),
      });
      setState(result);
      toast.success("Materials and tools saved");
    } catch (error) {
      toast.error(error);
      await load().catch(() => undefined);
    } finally {
      setBusy(false);
    }
  }

  if (!state) return <div className="space-y-3"><Skeleton className="h-24 w-full" /><Skeleton className="h-40 w-full" /></div>;

  const providerSlots = state.slots.filter((slot) => slot.source !== "manual");
  return (
    <div className="space-y-5 animate-panel-in">
      <section className="rounded-lg border border-border bg-background">
        <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/40 px-5 py-4">
          <div>
            <h2 className="text-sm font-semibold text-foreground">Materials &amp; tools</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">Loaded filament truth is kept here, independently from printer groups.</p>
          </div>
          <Button size="sm" onClick={() => void save()} loading={busy} disabled={!printer.access.can_print}>Save state</Button>
        </div>
        <div className="space-y-5 p-5">
          <label className="block max-w-xs space-y-1.5 text-xs font-medium text-foreground">
            Tool 0 nozzle diameter (mm)
            <Input type="number" min="0.1" max="5" step="0.01" value={nozzle} onChange={(event) => setNozzle(event.target.value)} disabled={!printer.access.can_print} placeholder="Unknown" />
            {state.tools.find((tool) => tool.tool_key === "tool0")?.source !== "manual" && <span className="block font-normal text-muted-foreground">Provider-reported nozzle is shown while fresh; saving creates the manual fallback.</span>}
          </label>

          {providerSlots.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Provider state</h3>
              <div className="grid gap-2 md:grid-cols-2">
                {providerSlots.map((slot) => (
                  <div key={`${slot.source}-${slot.slot_key}`} className="rounded-md border border-border bg-muted/20 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-foreground">{slot.label}</span>
                      <Badge variant={slot.stale ? "warning" : "outline"}>{slot.source.replace("_", " ")}</Badge>
                    </div>
                    <p className="mt-2 text-sm text-foreground">{slot.state === "loaded" ? [slot.material_brand, slot.material_type].filter(Boolean).join(" · ") : slot.state}</p>
                    <p className="mt-1 text-xs text-muted-foreground">{sourceLabel(slot)}{slot.observed_at ? ` · ${new Date(slot.observed_at).toLocaleString()}` : ""}</p>
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-3">
            <div className="flex items-center justify-between gap-3">
              <div><h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Manual feeds</h3><p className="mt-1 text-xs text-muted-foreground">Selecting a tracked spool does not mark it loaded; use “Set as loaded” explicitly.</p></div>
              <Button type="button" variant="outline" size="xs" disabled={!printer.access.can_print} onClick={addManualFeed}>Add feed</Button>
            </div>
            {slots.length === 0 && <p className="rounded-md border border-dashed border-border p-4 text-sm text-muted-foreground">No manual feeds. Add one for an external or unsupported filament path.</p>}
            {slots.map((slot, index) => (
              <div key={`${slot.slot_key}-${index}`} className="grid gap-3 rounded-md border border-border p-4 md:grid-cols-2 lg:grid-cols-4">
                <Input value={slot.label} onChange={(event) => patchSlot(index, { label: event.target.value })} disabled={!printer.access.can_print} aria-label="Feed label" />
                <select className={selectClassName} value={slot.state} onChange={(event) => patchSlot(index, { state: event.target.value as DraftSlot["state"] })} disabled={!printer.access.can_print}><option value="unknown">Unknown</option><option value="empty">Empty</option><option value="loaded">Loaded</option></select>
                <Input value={slot.material_type} onChange={(event) => patchSlot(index, { material_type: event.target.value })} disabled={!printer.access.can_print || slot.state !== "loaded"} placeholder="PLA" aria-label="Material type" />
                <Input value={slot.material_brand} onChange={(event) => patchSlot(index, { material_brand: event.target.value })} disabled={!printer.access.can_print || slot.state !== "loaded"} placeholder="Brand" aria-label="Material brand" />
                <label className="flex h-10 items-center gap-2 rounded-md border border-input px-3 text-xs text-muted-foreground">Color<input type="color" value={slot.color_hex} onChange={(event) => patchSlot(index, { color_hex: event.target.value.toUpperCase() })} disabled={!printer.access.can_print || slot.state !== "loaded"} /></label>
                {spoolmanEnabled && <select className={selectClassName} value={slot.spool_id ?? ""} onChange={(event) => patchSlot(index, { spool_id: event.target.value ? Number(event.target.value) : null })} disabled={!printer.access.can_print}><option value="">No tracked spool</option>{spools.map((spool) => <option key={spool.id} value={spool.id}>{spool.filament_name || spool.name || `Spool ${spool.id}`}</option>)}</select>}
                {slot.spool_id != null && slot.state !== "loaded" && <Button type="button" variant="outline" onClick={() => { const spool = spools.find((row) => row.id === slot.spool_id); patchSlot(index, { state: "loaded", material_type: spool?.material ?? slot.material_type, material_brand: spool?.vendor_name ?? slot.material_brand, color_hex: spool?.color_hex ? `#${spool.color_hex.replace(/^#/, "").slice(0, 6).toUpperCase()}` : slot.color_hex }); }}>Set as loaded</Button>}
                <Button type="button" variant="ghost" onClick={() => setSlots((current) => current.filter((_, slotIndex) => slotIndex !== index))} disabled={!printer.access.can_print}>Remove</Button>
                <p className="text-xs text-muted-foreground lg:col-span-4">Operator set{state.slots.find((row) => row.source === "manual" && row.slot_key === slot.slot_key)?.observed_at ? ` · updated ${new Date(state.slots.find((row) => row.source === "manual" && row.slot_key === slot.slot_key)!.observed_at!).toLocaleString()}` : " · not saved yet"}</p>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
