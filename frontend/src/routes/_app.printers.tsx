import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/printers"));

export const Route = createFileRoute("/_app/printers")({ component: Page });
