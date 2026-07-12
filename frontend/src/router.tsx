/* eslint-disable react-refresh/only-export-components */
import { createBrowserRouter } from "react-router-dom";
import { Suspense, lazy } from "react";

import RootLayout from "@/root-layout";

// After a rebuild the browser may still hold an index.html referencing an
// old chunk hash that no longer exists on disk, so the dynamic import 404s.
// Reload once to pick up the fresh index.html/chunk map; a session flag
// stops an infinite reload loop if the import keeps failing for another
// reason.
function lazyImport<T extends { default: React.ComponentType }>(
  factory: () => Promise<T>,
) {
  const key = "chunk-reload";
  return lazy(() =>
    factory()
      .then((mod) => {
        sessionStorage.removeItem(key);
        return mod;
      })
      .catch((err) => {
        if (!sessionStorage.getItem(key)) {
          sessionStorage.setItem(key, "1");
          window.location.reload();
          return new Promise<T>(() => {});
        }
        throw err;
      }),
  );
}

const HomePage = lazyImport(() => import("@/pages/home"));
const ModelDetailPage = lazyImport(() => import("@/pages/model-detail"));
const DocumentDetailPage = lazyImport(() => import("@/pages/document-detail"));
const LoginPage = lazyImport(() => import("@/pages/login"));
const SetupPage = lazyImport(() => import("@/pages/setup"));
const OrganizePage = lazyImport(() => import("@/pages/organize"));
const ProfilesPage = lazyImport(() => import("@/pages/profiles"));
const StatisticsPage = lazyImport(() => import("@/pages/statistics"));
const SettingsPage = lazyImport(() => import("@/pages/settings"));
const PrintersRoute = lazyImport(() => import("@/pages/printers"));
const PrinterDetailRoute = lazyImport(() => import("@/pages/printer-detail"));
const SharePage = lazyImport(() => import("@/pages/share"));
const NotFound = lazyImport(() => import("@/pages/not-found"));

function RouteChunk({ children }: { children: React.ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen w-full bg-background" aria-busy="true" />
      }
    >
      {children}
    </Suspense>
  );
}

export const router = createBrowserRouter([
  // Public, unauthenticated share viewer — deliberately mounted OUTSIDE
  // RootLayout so it bypasses auth/setup gating and the app chrome.
  { path: "share/:token", element: <RouteChunk><SharePage /></RouteChunk> },
  {
    element: <RootLayout />,
    children: [
      { index: true, element: <RouteChunk><HomePage /></RouteChunk> },
      { path: "models/:id", element: <RouteChunk><ModelDetailPage /></RouteChunk> },
      { path: "documents/:id", element: <RouteChunk><DocumentDetailPage /></RouteChunk> },
      { path: "login", element: <RouteChunk><LoginPage /></RouteChunk> },
      { path: "setup", element: <RouteChunk><SetupPage /></RouteChunk> },
      { path: "organize", element: <RouteChunk><OrganizePage /></RouteChunk> },
      { path: "profiles", element: <RouteChunk><ProfilesPage /></RouteChunk> },
      { path: "statistics", element: <RouteChunk><StatisticsPage /></RouteChunk> },
      { path: "settings", element: <RouteChunk><SettingsPage /></RouteChunk> },
      { path: "printers", element: <RouteChunk><PrintersRoute /></RouteChunk> },
      { path: "printers/:id", element: <RouteChunk><PrinterDetailRoute /></RouteChunk> },
      { path: "*", element: <RouteChunk><NotFound /></RouteChunk> },
    ],
  },
]);
