/** Navigation-local marks; no user data or remote telemetry. */
export type StartupMilestone =
  | "session-validated"
  | "library-requests"
  | "library-cards"
  | "library-tree"
  | "library-ready"
  | "library-thumbnails";

export function markStartup(milestone: StartupMilestone): void {
  if (!("mark" in performance) || !("getEntriesByName" in performance)) return;
  const name = `printstash:${milestone}`;
  if (performance.getEntriesByName(name).length === 0) performance.mark(name);
}
