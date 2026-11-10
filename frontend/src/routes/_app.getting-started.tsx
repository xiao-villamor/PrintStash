import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/getting-started"));

export const Route = createFileRoute("/_app/getting-started")({ component: Page });
