import { createFileRoute } from "@tanstack/react-router";
import RootLayout from "@/root-layout";
import { lazyImport } from "@/lib/lazy-component";
const NotFound = lazyImport(() => import("@/pages/not-found"));

// AuthProvider and SetupGate resolve authority before any private screen mounts.
// Data remains owned by Query hooks; no loader races the session boundary.
export const Route = createFileRoute("/_app")({
  component: RootLayout,
  notFoundComponent: NotFound,
});
