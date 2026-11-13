import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/statistics"));

export const Route = createFileRoute("/_app/statistics")({ component: Page });
