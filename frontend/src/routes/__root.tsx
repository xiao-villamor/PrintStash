import { createRootRoute } from "@tanstack/react-router";
import { RouteError } from "@/components/route-error";
import { RouteRoot } from "@/lib/route-root";

export const Route = createRootRoute({ component: RouteRoot, errorComponent: RouteError });
