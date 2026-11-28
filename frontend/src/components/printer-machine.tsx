import { useState } from "react";

import { Button } from "@/components/ui/button";
import type { useMachineSetup } from "@/features/printers/materials";
import { formatNumber } from "@/lib/format";
import { knownUiText, uiText } from "@/lib/locale";
import { printerArtwork } from "@/lib/orca-printer-images";
import { usePrinterArtworkPreference } from "@/lib/printer-card-display";
import { providerLabel } from "@/lib/printer-providers";
import type { MaterialSlotRead, PrinterRead } from "@/types";

export function MachinePortrait({ printer }: { printer: PrinterRead }) {
  const choice = usePrinterArtworkPreference();
  if (choice === "off") return null;
  const fallback = printerArtwork(null).imageUrl;
  const model = printer.model_name || printer.detected_model;
  const url = printerArtwork(choice === "model" ? model : null).imageUrl;
  return <PortraitImage key={url} url={url} fallback={fallback} />;
}

function PortraitImage({ url, fallback }: { url: string; fallback: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="printer-machine-portrait" aria-hidden="true">
      <img
        src={failed ? fallback : url}
        alt=""
        referrerPolicy="no-referrer"
        onError={() => setFailed(true)}
      />
    </div>
  );
}

export function MachineIdentity({
  printer,
  status,
  nozzle,
  summary,
}: {
  printer: PrinterRead;
  status: string;
  nozzle: number | null | undefined;
  summary?: { live: boolean; chamber?: number; cardPresent?: boolean; openSettings?: () => void };
}) {
  const model = printer.model_name || printer.detected_model;
  return (
    <section className="printer-desk-section" aria-label={uiText("Machine")}>
      <div className="printer-desk-heading">
        <h2>{uiText("Machine")}</h2>
        {summary && (
          <span className="inline-flex items-center gap-1.5">
            <span
              aria-hidden="true"
              className={`size-1.5 rounded-full ${summary.live ? "bg-success" : "bg-muted-foreground"}`}
            />
            {status}
          </span>
        )}
      </div>
      <div className="printer-machine-identity">
        <MachinePortrait printer={printer} />
        <div className="min-w-0 flex-1">
          <div className="break-words text-sm font-semibold">
            {model ?? uiText("Model unknown")}
          </div>
          <div className="mt-1 text-xs text-muted-foreground">
            {providerLabel(printer)}
            {summary && nozzle != null && (
              <>
                {" · "}
                <span className="sr-only">{uiText("Nozzle")}</span>
                <span>{uiText("{value1} mm", { value1: formatNumber(nozzle) })}</span>
              </>
            )}
          </div>
        </div>
        {summary?.openSettings && (
          <Button size="xs" variant="ghost" onClick={summary.openSettings}>
            {uiText("Printer settings")}
          </Button>
        )}
      </div>
      {summary?.live && (summary.chamber != null || summary.cardPresent != null) && (
        <dl className="printer-machine-facts">
          {summary.chamber != null && (
            <MachineFact
              label={uiText("Chamber")}
              value={uiText("{value1} °C", { value1: formatNumber(summary.chamber) })}
            />
          )}
          {summary.cardPresent != null && (
            <MachineFact
              label={uiText("SD card")}
              value={summary.cardPresent ? uiText("Inserted") : uiText("Not inserted")}
            />
          )}
        </dl>
      )}
      {!summary && (
        <dl className="printer-machine-facts">
          <MachineFact label={uiText("Connection")} value={status} />
          {printer.group && <MachineFact label={uiText("Group")} value={printer.group} />}
          <MachineFact label={uiText("Access")} value={knownUiText(printer.access.role)} />
          {nozzle != null && (
            <MachineFact
              label={uiText("Nozzle")}
              value={uiText("{value1} mm", { value1: formatNumber(nozzle) })}
            />
          )}
        </dl>
      )}
    </section>
  );
}

function MachineFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

export function MachineMaterials({
  setup,
  openMaterials,
  reportsMaterials,
  heading,
}: {
  setup: ReturnType<typeof useMachineSetup>;
  openMaterials?: () => void;
  reportsMaterials: boolean;
  heading?: string;
}) {
  const ams = new Map<string, Array<{ tray: string; slot: MaterialSlotRead }>>();
  const other: MaterialSlotRead[] = [];
  const external: MaterialSlotRead[] = [];
  for (const slot of setup.slots) {
    const match = slot.source === "bambu_ams" && /^ams:([^:]+):([^:]+)$/.exec(slot.slot_key);
    if (match) {
      const [, unit, tray] = match;
      const bank = ams.get(unit) ?? [];
      bank.push({ tray, slot });
      ams.set(unit, bank);
    } else if (
      slot.source === "bambu_ams" &&
      (slot.slot_key === "external" || slot.slot_key === "external:254")
    ) {
      external.push(slot);
    } else {
      other.push(slot);
    }
  }

  return (
    <section className="printer-desk-section" aria-label={uiText("Material setup")}>
      <div className="printer-desk-heading">
        <h2>{heading ?? uiText("Materials & tools")}</h2>
        {openMaterials && (
          <Button size="xs" variant="outline" onClick={openMaterials}>
            {uiText("Open materials")}
          </Button>
        )}
      </div>
      {setup.slots.length > 0 ? (
        <div className="space-y-3">
          {[...ams].map(([unit, trays]) => (
            <section
              key={unit}
              aria-label={uiText("AMS {value1}", { value1: unit })}
              className="space-y-2"
            >
              <h3 className="font-mono text-xs font-medium text-muted-foreground">
                {uiText("AMS {value1}", { value1: unit })}
              </h3>
              <ul className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
                {trays
                  .sort((left, right) =>
                    left.tray.localeCompare(right.tray, undefined, { numeric: true }),
                  )
                  .map(({ tray, slot }) => (
                    <li
                      key={slot.slot_key}
                      className="min-w-0 rounded-md border border-border bg-muted/30 p-2"
                    >
                      <MaterialSlot slot={slot} label={uiText("Tray {value1}", { value1: tray })} />
                    </li>
                  ))}
              </ul>
            </section>
          ))}
          {external.length > 0 && (
            <div className="border-t border-border pt-2">
              {external.map((slot) => (
                <MaterialSlot
                  key={slot.slot_key}
                  slot={slot}
                  label={
                    slot.slot_key === "external" && external.length === 1
                      ? uiText("External spool")
                      : uiText("External spool {value1}", {
                          value1: slot.slot_key === "external" ? "255" : "254",
                        })
                  }
                />
              ))}
            </div>
          )}
          {other.length > 0 && (
            <ul className="divide-y divide-border">
              {other.map((slot) => (
                <li key={slot.slot_key} className="py-2 first:pt-0 last:pb-0">
                  <MaterialSlot slot={slot} label={slot.label} />
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        reportsMaterials && (
          <p className="text-xs text-muted-foreground">
            {setup.isPending
              ? uiText("Loading material setup…")
              : setup.isError
                ? uiText("Material setup unavailable.")
                : uiText("No material slots reported.")}
          </p>
        )
      )}
    </section>
  );
}

export function PrinterFeedRows({ setup }: { setup: ReturnType<typeof useMachineSetup> }) {
  const feeds = setup.slots.filter(
    (slot) => slot.source === "manual" || slot.source === "moonraker_spoolman",
  );

  if (feeds.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        {setup.isPending
          ? uiText("Loading material setup…")
          : setup.isError
            ? uiText("Material setup unavailable.")
            : uiText("No material slots reported.")}
      </p>
    );
  }

  return (
    <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
      {feeds.map((feed) => (
        <li key={feed.slot_key} className="min-w-0">
          <MaterialSlot slot={feed} label={feed.label} feed />
        </li>
      ))}
    </ul>
  );
}

function MaterialSlot({
  slot,
  label,
  feed = false,
}: {
  slot: MaterialSlotRead;
  label: string;
  feed?: boolean;
}) {
  const color =
    slot.state === "loaded" && slot.color_hex && /^#[0-9a-f]{6}$/i.test(slot.color_hex)
      ? slot.color_hex
      : undefined;
  const loadedMaterial = slot.material_type ?? slot.spool_name;
  const stale = slot.stale && ` · ${uiText("Stale")}`;
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs">
      <span
        aria-label={color}
        aria-hidden={!color}
        className="printer-spool-winding shrink-0"
        data-state={slot.state}
        style={{ backgroundColor: color }}
        title={color}
      />
      <span className="min-w-0 break-words">
        {feed && slot.state === "loaded" && loadedMaterial ? (
          <>
            <span className="block font-medium">{loadedMaterial}</span>
            <span className="block text-muted-foreground">
              {label} · {uiText("Loaded")}
              {stale}
            </span>
          </>
        ) : (
          <>
            <span className="block text-muted-foreground">{label}</span>
            <span className="font-medium">
              {slot.state === "loaded"
                ? (loadedMaterial ?? uiText("Loaded"))
                : knownUiText(slot.state)}
              {stale}
            </span>
          </>
        )}
      </span>
    </div>
  );
}
