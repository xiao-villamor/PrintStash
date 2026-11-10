import { useEffect, useMemo, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { CollectionsPaneContext, useCollectionsPane } from "@/lib/collections-pane-context";
import { useAuth } from "@/lib/auth-context";
import { usePathname, useRouter } from "@/lib/navigation";
import { lastVaultHref } from "@/lib/last-collection";
import { readLibraryLocation } from "@/features/library/url";
import { readLibraryFilters } from "@/features/library/filters";
import { collectionIdentity } from "@/features/library/collection-navigation";
import { FilterSidebar } from "@/components/filter-sidebar";

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
  if (!user) throw new Error("Collections pane requires a private session");
  const storageKey = `printstash.collections-pane.${user.id}`;
  const [preference, setPreference] = useState(() => storedPreference(storageKey));
  const [host, attach] = useState<HTMLDivElement | null>(null);
  const [breadcrumbHost, attachBreadcrumbs] = useState<HTMLDivElement | null>(null);
  const open = preference ?? (pathname === "/" || pathname === "/search");
  useEffect(() => {
    if (preference === null) return;
    try {
      localStorage.setItem(storageKey, preference ? "open" : "closed");
    } catch {
      /* The pane remains usable without persistent preferences. */
    }
  }, [storageKey, preference]);
  const value = useMemo(
    () => ({
      host,
      attach,
      breadcrumbHost,
      attachBreadcrumbs,
      open,
      toggle: () => setPreference(!open),
    }),
    [host, breadcrumbHost, open],
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
  const { attach, open } = pane;
  return (
    <div
      ref={attach}
      id="collections-pane"
      data-collections-pane
      className={`h-full min-h-0 shrink-0 ${open ? "hidden md:flex" : "hidden"}`}
    >
      {pathname !== "/" && open ? <NavigationCollections /> : null}
    </div>
  );
}
