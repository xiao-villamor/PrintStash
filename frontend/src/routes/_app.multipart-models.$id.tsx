import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/multipart-model-detail"));

export const Route = createFileRoute("/_app/multipart-models/$id")({ component: Page });
