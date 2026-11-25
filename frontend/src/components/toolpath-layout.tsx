import { useState, type ReactNode } from "react";
import { TabBar } from "@/components/ui/tabs";
import { uiText } from "@/lib/locale";
import "./gcode-viewer.css";

export interface ToolpathControlContainers {
  layers: HTMLElement | null;
  view: HTMLElement | null;
}

/** Keep each control panel short enough to share the canvas's height. Hidden
 * panels stay mounted so changing tabs preserves layer and camera settings. */
export function ToolpathLayout({
  children,
  layers,
  view,
  viewAvailable = false,
}: {
  children: ReactNode;
  layers?: ReactNode;
  view?: ReactNode;
  viewAvailable?: boolean;
}) {
  const [selected, setSelected] = useState<"layers" | "view">("layers");
  const active = viewAvailable ? selected : "layers";
  return (
    <div className="toolpath-layout">
      <div className="toolpath-workspace">
        <div className="toolpath-viewport">{children}</div>
        <aside aria-label={uiText("Toolpath controls")} className="toolpath-sidebar">
          <TabBar
            tabs={
              viewAvailable
                ? [
                    { key: "layers", label: uiText("Layers") },
                    { key: "view", label: uiText("View") },
                  ]
                : [{ key: "layers", label: uiText("Layers") }]
            }
            active={active}
            onChange={setSelected}
            showIndicator={false}
            className="toolpath-control-tabs gap-0.5 rounded-md border border-border bg-muted p-0.5"
            tabClassName="min-h-8 flex-1 rounded-sm px-2.5 text-sm font-medium text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
            activeTabClassName="bg-card text-foreground shadow-xs"
          />
          <div className="toolpath-control-content">
            <div role="tabpanel" aria-label={uiText("Layers")} hidden={active !== "layers"}>
              {layers}
            </div>
            <div role="tabpanel" aria-label={uiText("View")} hidden={active !== "view"}>
              {view}
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}
