import { useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { getLibraryRevision } from "@/lib/api/library-browse";
import { onAuthChange, retirePrivateSessionScope } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import { subscribeEvents } from "@/lib/events";
import type { LibraryAuthority, LibraryBrowsePage } from "@/types/library-browse";
import { libraryBrowseKeys } from "./browse";

// Server tokens are opaque. Only client provenance has an ordering: a lookup
// started before a mount/page receipt cannot judge the newly displayed page.
let sequence = 0;
const nextSequence = () => ++sequence;
interface ProbeReceipt {
  requestSequence: number;
  receiptSequence: number;
  sessionVersion: number;
}
export interface LibraryAuthorityObservation extends ProbeReceipt {
  authority: LibraryAuthority;
}

/** Decode the foreign revision response before treating it as authority. */
function decodeLibraryAuthority(wire: LibraryAuthority): LibraryAuthority {
  const tokens = [wire?.browse_revision, wire?.authorization_revision];
  // oxlint-disable-next-line anti-slop/no-runtime-typeof -- HTTP JSON is foreign input; this decoder establishes both required nonempty opaque string tokens, without coercion or a fallback authority.
  if (!tokens.every((token) => typeof token === "string" && token.length > 0))
    throw new Error("invalid_library_authority");
  return {
    browse_revision: wire.browse_revision,
    authorization_revision: wire.authorization_revision,
  };
}

class BrowseReceipts {
  private receipt = 0;
  private listeners = new Set<() => void>();
  private stop: (() => void) | null = null;
  latestProbe: ProbeReceipt | null = null;
  constructor(private client: QueryClient) {}
  snapshot = () => this.receipt;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    if (!this.stop) {
      this.stop = this.client.getQueryCache().subscribe((event) => {
        const key = event.query.queryKey;
        if (
          event.type !== "updated" ||
          event.action.type !== "success" ||
          key[0] !== libraryBrowseKeys.all[0] ||
          key[1] !== libraryBrowseKeys.all[1]
        )
          return;
        // Includes structurally shared responses, prefetch and confirmed mutation
        // publication. Such receipts conservatively require one coalesced probe.
        this.receipt = nextSequence();
        for (const notify of this.listeners) notify();
      });
    }
    return () => {
      this.listeners.delete(listener);
      if (!this.listeners.size) {
        this.stop?.();
        this.stop = null;
      }
    };
  };
}
const receipts = new WeakMap<QueryClient, BrowseReceipts>();
function browseReceipts(client: QueryClient) {
  let owner = receipts.get(client);
  if (!owner) {
    owner = new BrowseReceipts(client);
    receipts.set(client, owner);
  }
  return owner;
}

export interface LibraryAuthorityOptions {
  enabled?: boolean;
  /** Connect notifications after initial critical rendering; authority probes stay immediate. */
  eventsReady?: boolean;
  /** The browse owner replaces its page explicitly; a notice never reorders it. */
  onRefresh: () => void | Promise<void>;
  /** Called after scope retirement. Re-verify getMe/roles here; handle its failure. */
  onAuthorityRetired: () => void;
}

/**
 * Pass the first displayed server page, or null while showing placeholder data.
 * Cached page remounts get a new mount boundary even when the object is shared.
 * One Query owns foreground polling, focus, reconnect and event resync; no
 * parallel freshness cache/timer exists. authorizationChanged must suppress
 * private output during render, before the effect clears its scope and assets.
 */
export function useLibraryAuthority(
  presentation: LibraryBrowsePage | null,
  { enabled = true, eventsReady = true, onRefresh, onAuthorityRetired }: LibraryAuthorityOptions,
) {
  const client = useQueryClient();
  const owner = browseReceipts(client);
  const receiptSequence = useSyncExternalStore(owner.subscribe, owner.snapshot, owner.snapshot);
  const sessionVersion = useSyncExternalStore(onAuthChange, getSessionVersion, getSessionVersion);
  const [mountedSession] = useState(getSessionVersion);
  const [mountSequence] = useState(nextSequence);
  const presentationSequence = useMemo(
    () => (presentation ? nextSequence() : mountSequence),
    [presentation, mountSequence],
  );
  const boundary = Math.max(mountSequence, presentationSequence);
  const active = enabled && presentation !== null && sessionVersion === mountedSession;
  const query = useQuery({
    queryKey: libraryBrowseKeys.authority,
    queryFn: async ({ signal }): Promise<LibraryAuthorityObservation> => {
      const provenance: ProbeReceipt = {
        requestSequence: nextSequence(),
        receiptSequence: owner.snapshot(),
        sessionVersion: getSessionVersion(),
      };
      owner.latestProbe = provenance;
      const authority = decodeLibraryAuthority(await getLibraryRevision({ signal }));
      return { authority, ...provenance };
    },
    enabled: active,
    staleTime: Infinity,
    retry: false,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
    refetchOnReconnect: "always",
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
  });
  const { refetch } = query;

  useEffect(() => {
    if (!active) return;
    const probe = owner.latestProbe;
    if (
      probe &&
      probe.requestSequence > boundary &&
      probe.receiptSequence === receiptSequence &&
      probe.sessionVersion === sessionVersion
    )
      return;
    let disposed = false;
    // refetch(cancelRefetch:true) does not cancel a pending initial fetch with
    // no cached data. Explicit cancellation also covers that page-arrival race.
    void client.cancelQueries({ queryKey: libraryBrowseKeys.authority, exact: true }).then(() => {
      if (!disposed && getSessionVersion() === sessionVersion)
        void refetch({ cancelRefetch: false });
    });
    return () => {
      disposed = true;
    };
  }, [active, boundary, client, owner, receiptSequence, refetch, sessionVersion]);

  const [hasPresentation, setHasPresentation] = useState(active && eventsReady);
  if (active && eventsReady && !hasPresentation) setHasPresentation(true);
  const connectEvents = enabled && hasPresentation && sessionVersion === mountedSession;
  const activePresentation = useRef(active);
  useLayoutEffect(() => {
    activePresentation.current = active;
  }, [active]);
  useEffect(() => {
    if (!connectEvents) return;
    // Keep the session transport while the next page is loading. Reopening it
    // on every destination competes with thumbnails and repeats the resync probe.
    return subscribeEvents((notice) => {
      if (notice.type === "resync" && activePresentation.current)
        void refetch({ cancelRefetch: false });
    });
  }, [connectEvents, refetch]);

  const observation = query.data;
  const current =
    active &&
    observation &&
    observation.requestSequence > boundary &&
    observation.receiptSequence === receiptSequence &&
    observation.sessionVersion === sessionVersion;
  const authorizationMismatch = Boolean(
    current &&
    presentation &&
    observation.authority.authorization_revision !== presentation.authorization_revision,
  );
  const authorizationChanged = sessionVersion !== mountedSession || authorizationMismatch;
  const refreshRequired = Boolean(
    current &&
    presentation &&
    !authorizationChanged &&
    observation.authority.browse_revision !== presentation.browse_revision,
  );
  const retired = useRef<number | null>(null);
  useEffect(() => {
    if (!authorizationMismatch || !observation || retired.current === observation.requestSequence)
      return;
    retired.current = observation.requestSequence;
    retirePrivateSessionScope();
    onAuthorityRetired();
  }, [authorizationMismatch, observation, onAuthorityRetired]);

  return {
    refreshRequired,
    authorizationChanged,
    checking: active && query.isFetching,
    error: active ? query.error : null,
    authority: current ? observation.authority : null,
    recheck: () => {
      if (active && !authorizationChanged && getSessionVersion() === mountedSession)
        return refetch({ cancelRefetch: false });
    },
    refresh: () => {
      if (active && !authorizationChanged && getSessionVersion() === mountedSession)
        return onRefresh();
    },
  };
}
