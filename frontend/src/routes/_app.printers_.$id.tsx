import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/printer-detail"));

export const Route = createFileRoute("/_app/printers_/$id")({ component: Page });
