/**
 * The arrange step for any component that talks to the API, routes, or reads the
 * session — which is most of `src/components/` and all of `src/pages/`.
 *
 * Every one of those tests needs the same five providers wired in the same order,
 * a `QueryClient` configured so seeded data stays put, and a `fetch` stub. Spelled
 * out per file that is sixty lines of boilerplate that drifts: one file forgets
 * `retry: false` and gets three network attempts per assertion, another forgets
 * `invalidateApiCache()` and inherits the previous test's 30-second GET cache.
 *
 * So the harness is here, once, and a test names only what it cares about:
 *
 *     const { routes } = renderApp(<ModelGrid />, {
 *       at: "/?collection=parts",
 *       routes: { "GET /api/v1/models": [aModelListItem()] },
 *     });
 *
 * What is *real* here matters as much as what is stubbed. The router, the query
 * hooks, the api client, the i18n catalog and the auth context are the production
 * ones — only `fetch` is stood in for, which is what lets a test pin the exact
 * HTTP request a form produces rather than assert that a mock was called.
 */

import { QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { clearLogin, storeLogin } from "@/lib/auth-store";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { invalidateApiCache } from "@/lib/api/request";
import { MobileFilterContext } from "@/lib/mobile-filter-context";
import { Toaster } from "@/components/toaster";
import { queryClient } from "@/lib/query-client";

/** A signed-in superuser — the ordinary case for an admin-facing page. */
export function adminSession(override?: Partial<AuthState>): AuthState {
  return {
    user: { id: 1, username: "admin", email: null, is_superuser: true },
    loading: false,
    login: vi.fn<AuthState["login"]>(),
    logout: vi.fn<AuthState["logout"]>(),
    refresh: vi.fn<AuthState["refresh"]>(),
    ...override,
  };
}

/** A signed-in user without admin rights, for the RBAC half of a page. */
export function memberSession(override?: Partial<AuthState>): AuthState {
  return adminSession({
    user: { id: 2, username: "maker", email: null, is_superuser: false },
    ...override,
  });
}

/**
 * Wrap a payload as the JSON response a route answers with.
 *
 * Every route answers a `Response` rather than a bare value, so a 200 and a 403
 * are written the same way and the payload keeps its own type all the way in —
 * `json(models)` stays `ModelListItem[]`, not a widened dictionary.
 */
export function json<T>(payload: T, status = 200): Response {
  // 204/205/304 are defined as bodiless, and the `Response` constructor throws
  // rather than ignoring one — so `json(null, 204)` has to mean "no content".
  const bodiless = status === 204 || status === 205 || status === 304;
  return new Response(bodiless ? null : JSON.stringify(payload), {
    status,
    headers: bodiless ? undefined : { "content-type": "application/json" },
  });
}

/**
 * What a stubbed route answers with: a response, or a function of the request
 * for a route whose answer depends on what was asked.
 */
export type RouteAnswer = Response | ((url: string, init?: RequestInit) => Response);

/**
 * `"<METHOD> <path prefix>"` → answer. The longest matching prefix wins, so a
 * specific route (`GET /api/v1/models/1`) overrides a general one
 * (`GET /api/v1/models`) regardless of declaration order.
 */
export type RouteTable = Record<string, RouteAnswer>;

export interface RenderAppOptions extends Omit<RenderOptions, "wrapper"> {
  /** The initial location, e.g. `"/?collection=parts"`. */
  at?: string;
  /**
   * The route pattern the component sits behind, e.g. `"/documents/:id"`.
   *
   * A page that reads `useParams()` gets nothing from a bare `MemoryRouter` —
   * the params come from the matched *route*, not from the URL. Without this a
   * detail page renders as though its id were missing, which is a different
   * screen from the one under test.
   */
  routePath?: string;
  /** The session the tree sees. Defaults to a superuser. */
  auth?: AuthState;
  /** Locale for the i18n provider. Defaults to English. */
  locale?: "en" | "es";
  /**
   * What `matchMedia` answers. jsdom has no layout, so every responsive branch
   * would otherwise throw — and a component that reads one is not "untested",
   * it is untestable. Defaults to the desktop side of every breakpoint, which is
   * where the full UI renders.
   */
  matchesMedia?: (query: string) => boolean;
  /** Query-cache entries to seed, so a render does not have to wait on the network. */
  seed?: [readonly unknown[], unknown][];
  /** HTTP the component may make. Anything unrouted answers 404. */
  routes?: RouteTable;
}

export interface RenderAppResult extends RenderResult {
  /** The application's query client, for asserting on cache invalidation. */
  client: typeof queryClient;
  /** Every request the component made, oldest first. */
  requests: () => { method: string; url: string; body: string }[];
  /** Requests matching one verb, oldest first. */
  requestsWithMethod: (method: string) => { url: string; body: string }[];
  /** The raw body of the last request, or `""` when there was none. */
  lastBody: () => string;
  /** Add or replace routes after the initial render. */
  route: (table: RouteTable) => void;
}

/** The route whose `"<METHOD> <prefix>"` best matches this request, if any. */
function matchRoute(
  table: Map<string, RouteAnswer>,
  method: string,
  url: string,
): RouteAnswer | undefined {
  const best = [...table.keys()]
    .filter((key) => {
      const [routeMethod, prefix] = key.split(" ", 2);
      return routeMethod === method && prefix !== undefined && url.startsWith(prefix);
    })
    .sort((a, b) => b.length - a.length)[0];
  return best === undefined ? undefined : table.get(best);
}

/**
 * Render a component with the whole application shell around it.
 *
 * Call `stubFetchGlobally()` from a `beforeEach` if a suite renders more than
 * once; `renderApp` installs the stub itself for the common single-render case.
 */
export function renderApp(ui: ReactElement, options: RenderAppOptions = {}): RenderAppResult {
  const {
    at = "/",
    routePath,
    auth = adminSession(),
    locale = "en",
    matchesMedia = () => true,
    seed = [],
    routes = {},
    ...rest
  } = options;

  window.localStorage.setItem("printstash.locale", locale);
  // `useRequireAuth` reads the stored session rather than the context, so a page
  // rendered with only the context provider looks signed *out* and hides every
  // write affordance. Both have to agree, exactly as they do after a real login.
  if (auth.user) storeLogin("", auth.user, { silent: true });
  else clearLogin();
  invalidateApiCache();

  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: matchesMedia(query),
    media: query,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  }));

  const table = new Map<string, RouteAnswer>(Object.entries(routes));
  const calls: { method: string; url: string; body: string }[] = [];
  const fetchStub = vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ method, url, body: String(init?.body ?? "") });
    const answer = matchRoute(table, method, url);
    // 404 rather than a hang or a throw: an unrouted call is a test that did not
    // say what it expected, and the component should show its own error path.
    if (answer === undefined) return json({ detail: "not_found" }, 404);
    // A `Response` body can only be read once, so a function route rebuilds it
    // per call; a static one is cloned for the same reason.
    return answer instanceof Function ? answer(url, init) : answer.clone();
  });
  vi.stubGlobal("fetch", fetchStub);

  // The application's own client, not a fresh one. `request.ts` invalidates
  // *this* cache after a mutation, so a component rendered against a private
  // client would never see the refresh a real one triggers — and the test would
  // be asserting a wiring that does not exist. `resetQueryCache()` clears it
  // between tests instead.
  const client = queryClient;
  client.clear();
  client.setDefaultOptions({
    // Seeded data must stay put: nothing here may quietly fall back to the network,
    // and a retry turns one deliberate failure into three.
    queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false },
  });
  for (const [key, value] of seed) client.setQueryData(key, value);

  // The app shell owns this in production; a component rendered without it
  // throws rather than degrading, so it belongs in every harness render.
  const mobileFilters = {
    open: false,
    openDrawer: vi.fn<() => void>(),
    closeDrawer: vi.fn<() => void>(),
  };

  function Providers({ children }: { children: ReactNode }) {
    const routed =
      routePath === undefined ? (
        children
      ) : (
        <Routes>
          <Route path={routePath} element={children} />
        </Routes>
      );
    return (
      <MemoryRouter initialEntries={[at]}>
        <QueryClientProvider client={client}>
          <AuthContext.Provider value={auth}>
            <MobileFilterContext.Provider value={mobileFilters}>
              <I18nProvider>
                {routed}
                {/* The real toaster, because a toast is how most write paths report
                    what happened — "the batch skipped two" has no other rendering.
                    Without it those outcomes are unassertable, which is how a test
                    ends up asserting that a mock was called instead. */}
                <Toaster />
              </I18nProvider>
            </MobileFilterContext.Provider>
          </AuthContext.Provider>
        </QueryClientProvider>
      </MemoryRouter>
    );
  }

  const result = render(ui, { wrapper: Providers, ...rest });

  return {
    ...result,
    client,
    requests: () => [...calls],
    requestsWithMethod: (method: string) =>
      calls.filter((call) => call.method === method.toUpperCase()),
    lastBody: () => calls.at(-1)?.body ?? "",
    route: (extra: RouteTable) => {
      for (const [key, answer] of Object.entries(extra)) table.set(key, answer);
    },
  };
}
