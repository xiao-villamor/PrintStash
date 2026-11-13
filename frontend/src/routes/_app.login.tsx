import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/login"));

export const Route = createFileRoute("/_app/login")({ component: Page });
