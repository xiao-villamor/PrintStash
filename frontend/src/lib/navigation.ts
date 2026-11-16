"use client";

/** Application navigation; server reads and Library restoration stay at their feature owners. */
import {
  useLocation,
  useRouter as useTanStackRouter,
  type HistoryState,
  type RouterHistory,
} from "@tanstack/react-router";
import { useLayoutEffect, useMemo, useState } from "react";

type NavigationOptions = { state?: HistoryState };
export interface AppRouter {
  push: (href: string, options?: NavigationOptions) => void;
  replace: (href: string, options?: NavigationOptions) => void;
  back: () => void;
  forward: () => void;
}

export function useRouter(): AppRouter {
  const router = useTanStackRouter();
  return useMemo(
    () => ({
      push: (href: string, options?: NavigationOptions) => {
        void router.navigate({ href, state: options?.state });
      },
      replace: (href: string, options?: NavigationOptions) => {
        void router.navigate({ href, replace: true, state: options?.state });
      },
      back: () => router.history.back(),
      forward: () => router.history.forward(),
    }),
    [router],
  );
}

export function usePathname(): string {
  return useLocation({ select: (location) => location.pathname });
}

/** Keep existing feature codecs authoritative, including repeated tag parameters. */
export function useSearchParams(): URLSearchParams {
  const search = useLocation({ select: (location) => location.searchStr });
  return useMemo(() => new URLSearchParams(search), [search]);
}

type HistoryEvent = Parameters<Parameters<RouterHistory["subscribe"]>[0]>[0];
/** Distinguish our URL acknowledgements from Back/Forward to an older draft. */
export function useHistoryAction() {
  const router = useTanStackRouter();
  const [action, setAction] = useState<HistoryEvent["action"]["type"] | null>(null);
  useLayoutEffect(() => router.history.subscribe(({ action }) => setAction(action.type)), [router]);
  return action;
}
