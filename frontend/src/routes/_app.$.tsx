import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";
const NotFound = lazyImport(() => import("@/pages/not-found"));
export const Route = createFileRoute("/_app/$")({ component: NotFound });
