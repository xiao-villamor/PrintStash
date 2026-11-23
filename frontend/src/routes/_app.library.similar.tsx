import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/similar-models"));

export const Route = createFileRoute("/_app/library/similar")({ component: Page });
