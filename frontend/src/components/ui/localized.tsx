import type { ReactNode } from "react";

/** Compatibility wrapper: text is now translated at its owning call site. */
export function Localized({ children }: { children: ReactNode }) {
  return <>{children}</>;
}
