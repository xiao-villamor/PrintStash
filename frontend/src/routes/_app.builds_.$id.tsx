import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/multipart-builds"));

export const Route = createFileRoute("/_app/builds_/$id")({ component: Page });
