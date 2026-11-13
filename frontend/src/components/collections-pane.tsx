import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
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
import { Box, Boxes } from "lucide-react";
import { LibrarySearch } from "@/components/library-search";
import { uiText } from "@/lib/locale";
import { useUiLocale } from "@/lib/i18n";
import { LibraryItemLink } from "@/features/library/navigation";
import type { LibraryEntry } from "@/features/library/navigation-state";
import { useContentReveal } from "@/lib/use-content-reveal";
import { useCollectionsPaneMotion } from "./use-collections-pane-motion";
import { PendingSidebar, SavedViewsSidebar } from "@/components/context-panels";

declare module "react" {
  interface CSSProperties {
    "--workbench-sidebar-width"?: string;
  }
}

function storedWidth() {
  try {
    const raw = localStorage.getItem("ps-sidebar-width");
    const width = raw === null ? 320 : Number(raw);
    return Number.isFinite(width) ? Math.max(280, Math.min(520, width)) : 320;
  } catch {
    return 320;
  }
}

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
  const [width, updateWidth] = useState(storedWidth);
  const setWidth = useCallback(
    (value: number) => updateWidth(Math.max(280, Math.min(520, value))),
    [],
  );
  useEffect(() => {
    try {
      localStorage.setItem("ps-sidebar-width", String(width));
    } catch {
      /* Session preference remains usable. */
    }
  }, [width]);
  const [preference, setPreference] = useState(() => storedPreference(storageKey));
  const [host, attach] = useState<HTMLDivElement | null>(null);
  const [breadcrumbHost, attachBreadcrumbs] = useState<HTMLDivElement | null>(null);
  const settings =
    pathname === "/settings" &&
    (searchParams.get("section") !== "trash" || searchParams.get("view") === "settings");
  const routeScope = `${pathname}:${searchParams.get("favorites") === "true"}:${settings}`;
  const [requestedMode, setRequestedMode] = useState<{
    scope: string;
    mode: "collections" | "search" | "pending" | "views";
  } | null>(null);
  // A temporary tool selection belongs to this visit, not a future return.
  if (requestedMode && requestedMode.scope !== routeScope) setRequestedMode(null);
  const mode =
    requestedMode?.scope === routeScope
      ? requestedMode.mode
      : pathname === "/search"
        ? "search"
        : pathname === "/" && searchParams.get("favorites") === "true"
          ? "favorites"
          : settings
            ? "settings"
            : "collections";
  // Section navigation never changes the user's sidebar geometry.
  const open = preference ?? true;
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
      width,
      setWidth,
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
    [host, breadcrumbHost, open, mode, routeScope, width, setWidth],
  );
  return (
    <CollectionsPaneContext.Provider value={value}>
      <div className="contents" style={{ "--workbench-sidebar-width": `${width}px` }}>
        {children}
      </div>
    </CollectionsPaneContext.Provider>
  );
}

/** Feature owners retain their navigation data while the shell owns the frame. */
export function WorkbenchSidebarContent({ children }: { children: ReactNode }) {
  const pane = useCollectionsPane();
  if (!pane) return children;
  return pane.host ? createPortal(children, pane.host) : null;
}

export { WorkbenchSidebarContent as LibraryCollectionsPane };

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
      onCollectionChange={(path, id = null) =>
        navigate((next) => {
          next.delete("cid");
          if (path && id !== null) next.set("cid", String(id));
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
  const motion = useCollectionsPaneMotion(open);
  const params = useSearchParams();
  const sectionMotion = useContentReveal(`${mode}:${pathname === "/" ? "library" : "navigation"}`, {
    enabled: open,
    scope: `${pathname}:${params.get("favorites") === "true"}`,
  });
  return (
    <div
      ref={motion.viewport}
      id="collections-pane"
      data-collections-pane
      data-open={open}
      inert={!open}
      aria-hidden={!open}
      className="collections-pane-shell hidden h-full min-h-0 shrink-0 md:flex"
    >
      <div ref={motion.content} className="collections-pane-content flex h-full min-h-0 shrink-0">
        <div ref={sectionMotion} className="workbench-section-content flex h-full w-full min-h-0">
          <div
            ref={attach}
            className={`flex h-full w-full min-h-0 ${mode !== "collections" && mode !== "favorites" && mode !== "settings" ? "hidden" : ""}`}
          >
            {pathname !== "/" && mode === "collections" ? <NavigationCollections /> : null}
          </div>
          {mode === "search" ? <SearchSidebar /> : null}
          {mode === "pending" ? <PendingSidebar /> : null}
          {mode === "views" ? <SavedViewsSidebar /> : null}
        </div>
      </div>
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
    <aside aria-label={uiText("Favorites")} className="flex h-full w-full shrink-0 flex-col">
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
  const key = `printstash.recent-searches.${user?.id}`;
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
  function rememberSearch(query: string) {
    const trimmed = query.trim();
    if (!trimmed || trimmed.length > 512) return;
    setRecent((previous) => [trimmed, ...previous.filter((item) => item !== trimmed)].slice(0, 8));
  }
  useEffect(() => {
    try {
      localStorage.setItem(key, JSON.stringify(recent));
    } catch {
      /* Recent queries remain usable in this tab when storage is unavailable. */
    }
  }, [key, recent]);
  return (
    <aside aria-label={uiText("Search panel")} className="flex h-full w-full shrink-0 flex-col">
      <h2 className="flex h-12 shrink-0 items-center px-4 font-mono text-2xs font-medium uppercase tracking-[.06em] text-muted-foreground">
        {uiText("Search")}
      </h2>
      <LibrarySearch placement="sidebar" recent={recent} onSearchCommit={rememberSearch} />
    </aside>
  );
}
