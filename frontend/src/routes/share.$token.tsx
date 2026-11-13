import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";
const Share = lazyImport(() => import("@/pages/share"));

// Public viewers bypass the private AuthProvider/SetupGate/chrome boundary.
export const Route = createFileRoute("/share/$token")({ component: Share });
