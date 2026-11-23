import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/document-detail"));

export const Route = createFileRoute("/_app/documents/$id")({ component: Page });
