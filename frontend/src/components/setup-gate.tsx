"use client";

/**
 * Gates the entire UI on the backend's setup status.
 *
 * - While the probe is in flight, renders a centered spinner so we don't
 *   flash the empty app shell.
 * - If the backend reports `configured === false`, force-redirects to
 *   `/setup` (no matter what URL the user hit). The Sidebar/TopBar chrome
 *   are hidden by the `usePathname() === "/setup"` check in layout.tsx so
 *   the wizard renders edge-to-edge.
 * - If `configured === true` and the user lands on `/setup`, send them to
 *   `/login` (or `/` if already authenticated — layout handles that via the
 *   AuthProvider).
 *
 * The probe is cheap (a single SELECT count(*)) and runs once per full page
 * load. We don't keep polling — once we know the install is configured, the
 * state can only change by destroying and recreating the DB.
 */

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "@/lib/navigation";
import { Loader2 } from "lucide-react";

import { getSetupStatus } from "@/lib/api";

interface Props {
  children: React.ReactNode;
}

export function SetupGate({ children }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSetupStatus()
      .then((status) => {
        if (cancelled) return;
        if (!status.configured && pathname !== "/setup") {
          router.replace("/setup");
          return; // keep `ready=false` until the next navigation re-mounts us
        }
        if (status.configured && pathname === "/setup") {
          router.replace("/login");
          return;
        }
        setReady(true);
      })
      .catch((err) => {
        if (cancelled) return;
        // If we can't even reach the backend, render children anyway so the
        // existing AuthBanner / api-error UI can surface what went wrong.
        // We just log this and unblock the tree.
        console.warn("setup status probe failed:", err);
        setError(err?.message ?? "unknown");
        setReady(true);
      });
    return () => {
      cancelled = true;
    };
    // We intentionally re-run on pathname changes so navigating from /setup
    // to / after completion re-validates immediately.
  }, [pathname, router]);

  if (!ready) {
    return (
      <div className="min-h-screen w-full flex items-center justify-center bg-surface-container-lowest">
        <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" />
      </div>
    );
  }

  // `error` is intentionally swallowed here — surfaced only via console — so
  // that a transient backend hiccup doesn't lock the user out of the cached UI.
  void error;

  return <>{children}</>;
}
