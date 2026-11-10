import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/profiles"));

export const Route = createFileRoute("/_app/profiles")({ component: Page });
