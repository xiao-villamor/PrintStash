import type { ReactNode } from "react";
import { uiText } from "@/lib/locale";
import "./gcode-viewer.css";

/** Keep the source picker usable while a plate loads or cannot be rendered. */
export function ToolpathLayout({ children, sidebar }: { children: ReactNode; sidebar: ReactNode }) {
  return (
    <div className="toolpath-layout">
      <div className="toolpath-workspace">
        <div className="toolpath-viewport">{children}</div>
        <aside aria-label={uiText("Toolpath controls")} className="toolpath-sidebar">
          {sidebar}
        </aside>
      </div>
    </div>
  );
}
