"use client";

import { useUiLocale } from "@/lib/i18n";

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
 * The probe runs once per path entry. Query owns cancellation and retry;
 * no background polling competes with explicit setup recovery.
 */

import { useEffect, useId, useState } from "react";
import { usePathname, useRouter } from "@/lib/navigation";
import { Loader2 } from "lucide-react";

import { useQuery } from "@tanstack/react-query";
import { setupGateOptions } from "@/features/setup/entry";
import { useAuth } from "@/lib/auth-context";

interface Props {
  children: React.ReactNode;
}

export function SetupGate({ children }: Props) {
  useUiLocale();
  const router = useRouter();
  const pathname = usePathname();
  const auth = useAuth();
  const isSuperuser = auth.user?.is_superuser === true;
  const entry = useId();
  const [navigation, setNavigation] = useState({ pathname, generation: 0 });
  if (navigation.pathname !== pathname)
    setNavigation({ pathname, generation: navigation.generation + 1 });
  const probe = useQuery(setupGateOptions(entry, navigation.generation));
  // This is the accepted entry decision, not a second status snapshot. A public
  // credential entry must survive its own intentional auth/cache retirement.
  const [acceptedAt, setAcceptedAt] = useState<number | null>(null);
  const status = probe.data;
  const choiceDecided = Boolean(status?.storage_choice_required) && !auth.loading;
  const toStorageStep = choiceDecided && isSuperuser && pathname !== "/getting-started";
  let redirect: "/setup" | "/login" | "/getting-started" | null = null;
  if (!probe.isPending && !probe.error && status) {
    if (!status.configured && pathname !== "/setup") redirect = "/setup";
    else if (status.configured && pathname === "/setup") redirect = "/login";
    else if (toStorageStep) redirect = "/getting-started";
  }
  const admitted =
    !probe.isPending &&
    (Boolean(probe.error) ||
      (Boolean(status) &&
        redirect === null &&
        (!status?.storage_choice_required || choiceDecided)));
  if (admitted && acceptedAt !== navigation.generation) setAcceptedAt(navigation.generation);
  const ready = admitted || acceptedAt === navigation.generation;

  useEffect(() => {
    if (probe.error) {
      // Preserve the existing fail-open policy so the auth/API error UI remains usable.
      console.warn("setup status probe failed:", probe.error);
    } else if (redirect) router.replace(redirect);
  }, [probe.error, redirect, router]);

  if (!ready) {
    return (
      <div className="min-h-screen w-full flex items-center justify-center bg-surface-container-lowest">
        <Loader2 className="h-6 w-6 animate-spin text-on-surface-variant" />
      </div>
    );
  }

  return <>{children}</>;
}
