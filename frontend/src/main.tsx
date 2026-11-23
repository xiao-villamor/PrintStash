import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "@tanstack/react-router";
import { QueryClientProvider } from "@tanstack/react-query";

import { queryClient } from "@/lib/query-client";
import { I18nProvider } from "@/lib/i18n";
import { activateLocale } from "@/lib/locale";
import { registerPwa } from "@/lib/pwa";
import { startTaskCenterSessionScope } from "@/lib/task-center";

import "@/fonts.css";
import "@/globals.css";
import { router } from "@/router";

const ReactQueryDevtools =
  import.meta.env.DEV && import.meta.env.VITE_QUERY_DEVTOOLS === "true"
    ? lazy(() =>
        import("@tanstack/react-query-devtools").then((m) => ({
          default: m.ReactQueryDevtools,
        })),
      )
    : null;

const stopTaskScope = startTaskCenterSessionScope();
import.meta.hot?.dispose(stopTaskScope);

// Match and load this destination's code alongside its language catalog.
// Screens still mount only after translations are ready; AuthProvider keeps
// their private reads behind session validation.
void Promise.all([activateLocale(), router.load()]).then(() => {
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <I18nProvider>
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
          {ReactQueryDevtools ? (
            <Suspense fallback={null}>
              <ReactQueryDevtools initialIsOpen={false} />
            </Suspense>
          ) : null}
        </QueryClientProvider>
      </I18nProvider>
    </StrictMode>,
  );
});

registerPwa();
