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
import NotFound from "@/pages/not-found";

export const router = createBrowserRouter([
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
