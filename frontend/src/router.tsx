import { createBrowserRouter } from "react-router-dom";

import RootLayout from "@/root-layout";
import HomePage from "@/pages/home";
import ModelDetailPage from "@/pages/model-detail";
import LoginPage from "@/pages/login";
import SetupPage from "@/pages/setup";
import OrganizePage from "@/pages/organize";
import ProfilesPage from "@/pages/profiles";
import SettingsPage from "@/pages/settings";
import PrintersRoute from "@/pages/printers";
import PrinterDetailRoute from "@/pages/printer-detail";
import SharePage from "@/pages/share";
import NotFound from "@/pages/not-found";

export const router = createBrowserRouter([
  // Public, unauthenticated share viewer — deliberately mounted OUTSIDE
  // RootLayout so it bypasses auth/setup gating and the app chrome.
  { path: "share/:token", element: <SharePage /> },
  {
    element: <RootLayout />,
    children: [
      { index: true, element: <HomePage /> },
      { path: "models/:id", element: <ModelDetailPage /> },
      { path: "login", element: <LoginPage /> },
      { path: "setup", element: <SetupPage /> },
      { path: "organize", element: <OrganizePage /> },
      { path: "profiles", element: <ProfilesPage /> },
      { path: "settings", element: <SettingsPage /> },
      { path: "printers", element: <PrintersRoute /> },
      { path: "printers/:id", element: <PrinterDetailRoute /> },
      { path: "*", element: <NotFound /> },
    ],
  },
]);
