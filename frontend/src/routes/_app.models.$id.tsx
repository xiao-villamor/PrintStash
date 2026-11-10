import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/model-detail"));

export const Route = createFileRoute("/_app/models/$id")({ component: Page });
