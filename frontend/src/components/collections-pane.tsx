import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import {
  CollectionsPaneContext,
  useCollectionsPane,
  type CollectionsPaneState,
} from "@/lib/collections-pane-context";
import { useAuth } from "@/lib/auth-context";
import { usePathname, useRouter, useSearchParams } from "@/lib/navigation";
import { lastVaultHref } from "@/lib/last-collection";
import { readLibraryLocation } from "@/features/library/url";
import { readLibraryFilters } from "@/features/library/filters";
import { collectionIdentity } from "@/features/library/collection-navigation";
import { FilterSidebar } from "@/components/filter-sidebar";
import { Box, Boxes, History, Search } from "lucide-react";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { PendingSidebar, SavedViewsSidebar } from "@/components/context-panels";

function storedPreference(key: string): boolean | null {
  try {
    const value = localStorage.getItem(key);
    return value === "open" ? true : value === "closed" ? false : null;
  } catch {
    return null;
  }
}

/** Shell geometry is independent of Library entity selection and mutation scopes. */
export function CollectionsPaneProvider({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  if (!user) throw new Error("Collections pane requires a private session");
  const storageKey = `printstash.collections-pane.${user.id}`;
  const [preference, setPreference] = useState(() => storedPreference(storageKey));
  const [host, attach] = useState<HTMLDivElement | null>(null);
  const [breadcrumbHost, attachBreadcrumbs] = useState<HTMLDivElement | null>(null);
  const routeScope = `${pathname}:${searchParams.get("favorites") === "true"}`;
  const [requestedMode, setRequestedMode] = useState<{
    scope: string;
    mode: "collections" | "search" | "pending" | "views";
  } | null>(null);
  const mode =
    requestedMode?.scope === routeScope
      ? requestedMode.mode
      : pathname === "/search"
        ? "search"
        : pathname === "/" && searchParams.get("favorites") === "true"
          ? "favorites"
          : "collections";
  const open =
    preference ??
    (pathname === "/" ||
      pathname === "/search" ||
      pathname.startsWith("/inbox") ||
      pathname.startsWith("/multipart-models/") ||
      pathname.startsWith("/documents/") ||
      pathname.startsWith("/builds") ||
      pathname.startsWith("/library/similar"));
  useEffect(() => {
    if (preference === null) return;
    try {
      localStorage.setItem(storageKey, preference ? "open" : "closed");
    } catch {
      /* The pane remains usable without persistent preferences. */
    }
  }, [storageKey, preference]);
  const value = useMemo<CollectionsPaneState>(
    () => ({
      host,
      attach,
      breadcrumbHost,
      attachBreadcrumbs,
      open,
      toggle: () => setPreference(!open),
      mode,
      showCollections: () => {
        setPreference(true);
        setRequestedMode({ scope: routeScope, mode: "collections" });
      },
      showSearch: () => {
        setPreference(true);
        setRequestedMode({ scope: routeScope, mode: "search" });
      },
      showPending: () => {
        setPreference(true);
        setRequestedMode({ scope: routeScope, mode: "pending" });
      },
      showSavedViews: () => {
        setPreference(true);
        setRequestedMode({ scope: routeScope, mode: "views" });
      },
    }),
    [host, breadcrumbHost, open, mode, routeScope],
  );
  return (
    <CollectionsPaneContext.Provider value={value}>{children}</CollectionsPaneContext.Provider>
  );
}

/** The Library keeps the exact existing sidebar instance at its feature owner. */
export function LibraryCollectionsPane({ children }: { children: ReactNode }) {
  const pane = useCollectionsPane();
  if (!pane) return children;
  return pane.host ? createPortal(children, pane.host) : null;
}

/** Collection breadcrumb data and commands remain in the mounted Library owner. */
export function LibraryBreadcrumbs({ children }: { children: ReactNode }) {
  const pane = useCollectionsPane();
  if (!pane) return children;
  return pane.breadcrumbHost ? createPortal(children, pane.breadcrumbHost) : null;
}

function NavigationCollections() {
  const { user } = useAuth();
  const router = useRouter();
  const source = new URL(lastVaultHref(), window.location.origin);
  const location = readLibraryLocation(source.searchParams);
  const parsed = readLibraryFilters(source.searchParams, location, user?.is_superuser === true);
  const { filters, baseFilters, params } = parsed;
  function navigate(change: (params: URLSearchParams) => void) {
    const next = new URLSearchParams(params);
    change(next);
    router.push(`/?${next}`);
  }
  return (
    <FilterSidebar
      collectionsOnly
      navigationOnly
      filtersOpen={false}
      readinessEnabled={false}
      outlinerFilters={baseFilters}
      tags={[]}
      printers={[]}
      selectedCollection={filters.collection}
      selectedCollectionId={collectionIdentity(params)}
      selectedTags={filters.tag}
      selectedPrinterId={filters.printer_id}
      selectedPrinterPresence={filters.printer_presence}
      onCollectionChange={(path) =>
        navigate((next) => {
          next.delete("cid");
          if (path) next.set("c", path);
          else next.delete("c");
        })
      }
      onTagsChange={(tags) =>
        navigate((next) => {
          next.delete("tag");
          for (const tag of tags) next.append("tag", tag);
        })
      }
      onPrinterChange={(id) =>
        navigate((next) => {
          next.delete("printer_presence");
          if (id === null) next.delete("printer_id");
          else next.set("printer_id", String(id));
        })
      }
      onPrinterPresenceChange={(presence) =>
        navigate((next) => {
          next.delete("printer_id");
          if (presence === null) next.delete("printer_presence");
          else next.set("printer_presence", presence);
        })
      }
      onCreateCollection={() => navigate((next) => next.set("newCollection", "1"))}
      canViewPrinters={user?.is_superuser === true}
      canManageLibrary={user?.is_superuser === true}
      libraryView={location.view}
      onLibraryViewChange={(view) => navigate((next) => next.set("type", view))}
    />
  );
}

/** A full-height sibling of the content column; TopBar starts after this pane. */
export function CollectionsPaneHost() {
  const pane = useCollectionsPane();
  const pathname = usePathname();
  if (!pane) throw new Error("Collections pane host requires its shell provider");
  const { attach, open, mode } = pane;
  return (
    <div
      id="collections-pane"
      data-collections-pane
      className={`h-full min-h-0 shrink-0 ${open ? "hidden md:flex" : "hidden"}`}
    >
      <div
        ref={attach}
        className={`flex h-full min-h-0 ${mode !== "collections" && mode !== "favorites" ? "hidden" : ""}`}
      >
        {pathname !== "/" && open && mode === "collections" ? <NavigationCollections /> : null}
      </div>
      {open && mode === "search" ? <SearchSidebar /> : null}
      {open && mode === "pending" ? <PendingSidebar /> : null}
      {open && mode === "views" ? <SavedViewsSidebar /> : null}
    </div>
  );
}

export function FavoritesSidebar({
  models,
  multipartModels,
  origin,
}: {
  models: readonly { id: number; name: string }[];
  multipartModels: readonly { id: number; name: string }[];
  origin?: LibraryEntry;
}) {
  useUiLocale();
  return (
    <aside
      aria-label={uiText("Favorites")}
      className="flex h-full w-[321px] shrink-0 flex-col border-r border-border bg-sidebar"
    >
      <h2 className="flex h-12 shrink-0 items-center px-4 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
        {uiText("Favorites")}
      </h2>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {models.map((model) => (
          <LibraryItemLink
            origin={origin}
            key={model.id}
            href={`/models/${model.id}`}
            className="flex h-[30px] items-center gap-2 px-2.5 text-[13px] text-foreground hover:bg-muted"
          >
            <Box
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              strokeWidth={1.6}
              aria-hidden
            />
            <span className="truncate">{model.name}</span>
          </LibraryItemLink>
        ))}
        {multipartModels.map((model) => (
          <LibraryItemLink
            origin={origin}
            key={model.id}
            href={`/multipart-models/${model.id}`}
            className="flex h-[30px] items-center gap-2 px-2.5 text-[13px] text-foreground hover:bg-muted"
          >
            <Boxes
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              strokeWidth={1.6}
              aria-hidden
            />
            <span className="truncate">{model.name}</span>
          </LibraryItemLink>
        ))}
      </div>
    </aside>
  );
}

function SearchSidebar() {
  useUiLocale();
  const { user } = useAuth();
  const params = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();
  const q = params.get("q") ?? "";
  const key = `printstash.recent-searches.${user?.id}`;
  const [value, setValue] = useState(q);
  const [recent, setRecent] = useState<string[]>(() => {
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(key) ?? "[]");
      if (!Array.isArray(stored)) return [];
      return stored
        .filter((item): item is string => {
          // oxlint-disable-next-line anti-slop/no-runtime-typeof -- Parsed local storage is the untyped boundary; only string queries belong in this user's recent list.
          return typeof item === "string";
        })
        .slice(0, 8);
    } catch {
      return [];
    }
  });
  const searchIdentity =
    pathname === "/search" && q.trim() && q.length <= 512 ? `${key}:${q}` : null;
  const [recordedSearch, setRecordedSearch] = useState<string | null>(null);
  if (recordedSearch !== searchIdentity) {
    setRecordedSearch(searchIdentity);
    if (searchIdentity !== null) {
      setRecent((previous) =>
        [q.trim(), ...previous.filter((item) => item !== q.trim())].slice(0, 8),
      );
      setValue(q);
    }
  }
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(recent));
    } catch {
      /* Recent queries remain usable in this tab when storage is unavailable. */
    }
  }, [key, recent]);
  function search(query: string) {
    router.push(`/search?${new URLSearchParams({ q: query })}`);
  }
  return (
    <aside
      aria-label={uiText("Search panel")}
      className="flex h-full w-[321px] shrink-0 flex-col border-r border-border bg-sidebar"
    >
      <h2 className="flex h-12 shrink-0 items-center px-4 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
        {uiText("Search")}
      </h2>
      <form
        className="relative mx-2.5 mb-5"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) search(value.trim());
        }}
      >
        <Search
          className="pointer-events-none absolute left-2 top-2.5 h-3.5 w-3.5 text-muted-foreground"
          aria-hidden
        />
        <input
          data-shell-panel-search
          type="search"
          aria-label={uiText("Search library")}
          placeholder={uiText("Name, tag, file or what it's for")}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          className="h-[34px] w-full border border-input bg-card pl-7 pr-2 text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
      </form>
      <h3 className="mb-2 px-4 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
        {uiText("Recent")}
      </h3>
      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 pb-3">
        {recent.map((query) => (
          <button
            key={query}
            type="button"
            onClick={() => {
              setValue(query);
              search(query);
            }}
            className="flex h-[30px] w-full items-center gap-2 px-2.5 text-left text-[13px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
          >
            <History
              className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
              strokeWidth={1.6}
              aria-hidden
            />
            <span className="truncate">{query}</span>
          </button>
        ))}
      </div>
    </aside>
  );
}
