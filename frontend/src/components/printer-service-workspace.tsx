import { useEffect, useRef, useState } from "react";
import { FleetMaintenancePanel } from "@/components/fleet-panels";
import { PrinterMaterials } from "@/components/printer-materials";
import { uiText } from "@/lib/locale";
import type { PrinterRead } from "@/types";

export function PrinterServiceWorkspace({
  printer,
  section,
  onRoutingChanged,
}: {
  printer: PrinterRead;
  section: "materials" | "maintenance";
  onRoutingChanged: () => void;
}) {
  const maintenance = useRef<HTMLElement>(null);
  const [actions, setActions] = useState<HTMLDivElement | null>(null);

  useEffect(() => {
    if (section === "maintenance") maintenance.current?.focus();
  }, [section]);

  return (
    <div className="printer-sections-split">
      {printer.access.can_print && (
        <section aria-label={uiText("Materials & tools")} className="printer-sections-panel">
          <header>
            <h2>{uiText("Materials & tools")}</h2>
          </header>
          <PrinterMaterials printer={printer} inWorkspace />
        </section>
      )}
      {printer.access.can_admin && (
        <section
          ref={maintenance}
          tabIndex={-1}
          aria-label={uiText("Maintenance")}
          className="printer-sections-panel focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <header>
            <h2>{uiText("Maintenance")}</h2>
            <div ref={setActions} />
          </header>
          <FleetMaintenancePanel
            compact
            actionsHost={actions}
            printers={[printer]}
            onPrintersChanged={onRoutingChanged}
          />
        </section>
      )}
    </div>
  );
}
