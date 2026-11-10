import { createRouter } from "@tanstack/react-router";
import { recoverRouteChunk } from "@/lib/lazy-component";
import { routeTree } from "@/routeTree.gen";
import { parseAppSearch, stringifyAppSearch } from "@/lib/router-search";

export const router = createRouter({
  routeTree,
  defaultOnCatch: (error) => {
    if (error instanceof Error) recoverRouteChunk(error);
  },
  parseSearch: parseAppSearch,
  stringifySearch: stringifyAppSearch,
  // Existing feature intent prefetch owns entity reads; links never fetch 3D bytes.
  defaultPreload: false,
  // Library owns its nested containers and history anchors.
  scrollRestoration: false,
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
  interface HistoryState {
    libraryOrigin?: string;
    similarityFilterOwner?: string;
  }
}
