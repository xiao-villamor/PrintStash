import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/setup"));

export const Route = createFileRoute("/_app/setup")({ component: Page });
