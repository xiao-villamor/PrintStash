import { createFileRoute } from "@tanstack/react-router";
import { lazyImport } from "@/lib/lazy-component";

const Page = lazyImport(() => import("@/pages/similar-model-comparison"));

export const Route = createFileRoute("/_app/library/similar_/$id")({ component: Page });
