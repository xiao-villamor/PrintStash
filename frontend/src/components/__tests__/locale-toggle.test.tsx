import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it } from "vitest";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { LocaleToggle } from "@/components/locale-toggle";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";

// An admin session, supplied through the real context so the admin-only nav
// entries (Printers) render. Nothing here logs in or out, so the commands are
// inert.
const adminAuth: AuthState = {
  user: { id: 1, username: "admin", email: null, is_superuser: true },
  loading: false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
};

beforeEach(() => localStorage.setItem("printstash.locale", "en"));

it("updates navigation menu labels when locale changes", async () => {
  render(
    <MemoryRouter>
      <AuthContext.Provider value={adminAuth}>
        <I18nProvider>
          <LocaleToggle />
          <BottomNavBar />
        </I18nProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );

  expect(screen.getByText("Vault")).toBeInTheDocument();
  expect(screen.getByText("Printers")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: /Language/ }));

  expect(screen.getByText("Bóveda")).toBeInTheDocument();
  expect(screen.getByText("Impresoras")).toBeInTheDocument();
});
