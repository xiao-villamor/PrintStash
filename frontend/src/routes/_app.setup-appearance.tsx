import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/setup-appearance"));

export const Route = createFileRoute("/_app/setup-appearance")({ component: Page });
