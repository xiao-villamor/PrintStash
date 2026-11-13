import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/settings"));

export const Route = createFileRoute("/_app/settings")({ component: Page });
