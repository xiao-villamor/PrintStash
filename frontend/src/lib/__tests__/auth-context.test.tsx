import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { AuthProvider, useAuth } from "@/lib/auth-context";
import { storeLogin } from "@/lib/auth-store";
import { logout as apiLogout } from "@/lib/api";
import { getMe } from "@/lib/api";

vi.mock("@/lib/api", () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  logout: vi.fn(),
}));

function AuthProbe() {
  const { loading, user, logout } = useAuth();
  if (loading) return <div>loading</div>;
  return (
    <div>
      {user ? `signed in:${user.username}` : "signed out"}
      <button onClick={() => void logout()}>Log out</button>
    </div>
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
});

describe("AuthProvider", () => {
  it("observes first-run setup login in the same tab", async () => {
    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    await screen.findByText("signed out");

    act(() => {
      storeLogin("setup-token", {
        id: 1,
        username: "admin",
        email: null,
        is_superuser: true,
      });
    });

    await waitFor(() => {
      expect(screen.getByText("signed in:admin")).toBeTruthy();
    });
  });

  it("revokes server session before clearing browser login", async () => {
    storeLogin("access-token", {
      id: 1,
      username: "admin",
      email: null,
      is_superuser: true,
    });
    vi.mocked(apiLogout).mockResolvedValue(undefined);
    vi.mocked(getMe).mockResolvedValue({
      id: 1,
      username: "admin",
      email: null,
      is_superuser: true,
      is_active: true,
      created_at: "2026-07-13T00:00:00Z",
      updated_at: "2026-07-13T00:00:00Z",
    });

    render(
      <AuthProvider>
        <AuthProbe />
      </AuthProvider>,
    );

    await screen.findByText("signed in:admin");
    fireEvent.click(screen.getByRole("button", { name: "Log out" }));

    await waitFor(() => expect(apiLogout).toHaveBeenCalledOnce());
    expect(window.localStorage.getItem("printstash.token")).toBeNull();
    expect(screen.getByText("signed out")).toBeTruthy();
  });
});
