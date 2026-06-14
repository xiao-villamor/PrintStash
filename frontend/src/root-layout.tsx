import { Outlet } from "react-router-dom";

import { AppShell } from "@/components/app-shell";
import { SetupGate } from "@/components/setup-gate";
import { AuthProvider } from "@/lib/auth-context";

/**
 * Root layout: mirrors the old Next `app/layout.tsx` provider stack, with
 * <Outlet/> rendering the matched route. AuthProvider → SetupGate → AppShell
 * wrap every page; AppShell decides chrome vs chromeless from the pathname.
 */
export default function RootLayout() {
  return (
    <AuthProvider>
      <SetupGate>
        <AppShell>
          <Outlet />
        </AppShell>
      </SetupGate>
    </AuthProvider>
  );
}
