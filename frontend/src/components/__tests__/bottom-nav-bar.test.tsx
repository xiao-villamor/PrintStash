import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { BottomNavBar } from "@/components/bottom-nav-bar";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { usePathname } from "@/lib/navigation";

const adminAuth: AuthState = {
  user: { id: 1, username: "admin", email: null, is_superuser: true },
  loading: false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
};

function CurrentPath() {
  return <output data-testid="current-path">{usePathname()}</output>;
}

function renderNavigation(initialEntry = "/") {
  return render(
    <MemoryRouter initialEntries={[initialEntry]}>
      <AuthContext.Provider value={adminAuth}>
        <I18nProvider>
          <BottomNavBar />
          <CurrentPath />
        </I18nProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

describe("Pending Imports navigation", () => {
  it("keeps Pending reachable and selected in the mobile bottom bar", async () => {
    const user = userEvent.setup();
    renderNavigation();

    const pending = screen.getByRole("link", { name: "Pending" });
    expect(pending).toHaveAttribute("href", "/inbox");

    await user.click(pending);

    expect(screen.getByTestId("current-path")).toHaveTextContent("/inbox");
    expect(pending).toHaveAttribute("aria-current", "page");
  });

  it("marks Pending active for a nested inbox route", () => {
    renderNavigation("/inbox/41");

    expect(screen.getByRole("link", { name: "Pending" })).toHaveAttribute("aria-current", "page");
  });
});
