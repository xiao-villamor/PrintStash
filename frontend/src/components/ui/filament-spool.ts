import { createLucideIcon } from "lucide-react";

export const FilamentSpool = createLucideIcon("filament-spool", [
  ["ellipse", { cx: "8.5", cy: "11", rx: "5.5", ry: "7.5", key: "rim" }],
  ["ellipse", { cx: "8.5", cy: "11", rx: "1.5", ry: "2", key: "hub" }],
  ["path", { d: "M8.5 3.5h6a5.5 7.5 0 0 1 0 15h-6", key: "spool" }],
  ["path", { d: "M18 16.8h1a2 2 0 0 1 2 2v1.7", key: "filament" }],
]);
