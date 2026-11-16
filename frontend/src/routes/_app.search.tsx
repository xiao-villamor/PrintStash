import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/search"));

export const Route = createFileRoute("/_app/search")({ component: Page });
