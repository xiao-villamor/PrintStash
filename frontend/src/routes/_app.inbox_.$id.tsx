import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/inbox-detail"));

export const Route = createFileRoute("/_app/inbox_/$id")({ component: Page });
