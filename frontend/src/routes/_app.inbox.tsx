import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/inbox"));

export const Route = createFileRoute("/_app/inbox")({ component: Page });
