/** A real TanStack memory router, with optional route elements for component integration tests. */
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { createContext, useContext, useState, type ReactNode } from "react";
import { parseAppSearch, stringifyAppSearch } from "@/lib/router-search";

export interface TestRoute {
  path: string;
  element: ReactNode;
}
interface TestRouterProps {
  children?: ReactNode;
  initialEntries?: string[];
  initialIndex?: number;
  routePath?: string;
  routes?: TestRoute[];
}

const ContentContext = createContext<ReactNode>(null);
const RoutesContext = createContext<TestRoute[]>([]);
function Content() {
  return useContext(ContentContext);
}
function RouteContent({ index }: { index: number }) {
  return useContext(RoutesContext)[index]?.element;
}

export function TestRouter({
  children,
  initialEntries = ["/"],
  initialIndex,
  routePath,
  routes,
}: TestRouterProps) {
  const [router] = useState(() => {
    const history = createMemoryHistory({ initialEntries });
    if (initialIndex !== undefined) history.go(initialIndex - initialEntries.length + 1);
    const root = createRootRoute({ component: routePath ? Outlet : Content });
    const definitions = routes ?? [{ path: routePath ?? "/$", element: null }];
    const tree = root.addChildren(
      definitions.map(({ path }, index) =>
        createRoute({
          getParentRoute: () => root,
          path: path.replace(/:([a-zA-Z]+)/g, "$$$1"),
          component: routePath ? Content : () => <RouteContent index={index} />,
        }),
      ),
    );
    const instance = createRouter({
      routeTree: tree,
      history,
      parseSearch: parseAppSearch,
      stringifySearch: stringifyAppSearch,
      defaultPendingMinMs: 0,
    });
    // The test tree has no loaders. Prime its matched snapshot synchronously so
    // component assertions can run immediately; later transitions use the router.
    instance.stores.setMatches(
      instance
        .matchRoutes(instance.state.location)
        .map((match) => ({ ...match, status: "success" as const })),
    );
    instance.stores.resolvedLocation.set(instance.state.location);
    return instance;
  });
  return (
    <ContentContext.Provider value={children}>
      <RoutesContext.Provider value={routes ?? []}>
        <RouterProvider router={router} />
      </RoutesContext.Provider>
    </ContentContext.Provider>
  );
}
