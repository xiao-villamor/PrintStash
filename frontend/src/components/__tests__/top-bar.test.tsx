import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopBar } from "@/components/top-bar";
import { AuthContext, type AuthState } from "@/lib/auth-context";
import { I18nProvider } from "@/lib/i18n";
import { usePathname } from "@/lib/navigation";

const fetchMock = vi.fn<typeof fetch>();

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
          <TopBar />
          <CurrentPath />
        </I18nProvider>
      </AuthContext.Provider>
    </MemoryRouter>,
  );
}

beforeEach(() => {
  fetchMock.mockReset();
  fetchMock.mockResolvedValue(
    new Response("[]", { status: 200, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => vi.unstubAllGlobals());

describe("Pending Imports navigation", () => {
  it("keeps Pending reachable and selected in the desktop profile menu", async () => {
    const user = userEvent.setup();
    renderNavigation();

    await user.click(screen.getByRole("button", { name: /admin/ }));
    const pending = screen.getByRole("menuitem", { name: "Pending" });
    expect(pending).toHaveAttribute("href", "/inbox");

    await user.click(pending);

    expect(screen.getByTestId("current-path")).toHaveTextContent("/inbox");
    await user.click(screen.getByRole("button", { name: /admin/ }));
    expect(screen.getByRole("menuitem", { name: "Pending" })).toHaveAttribute(
      "aria-current",
      "page",
    );
  });
});
