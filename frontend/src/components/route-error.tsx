import { PageFailure } from "@/components/page-failure";

/** Eager recovery UI remains available when a deferred route cannot load. */
export function RouteError() {
  return <PageFailure kind="unavailable" reload />;
}
