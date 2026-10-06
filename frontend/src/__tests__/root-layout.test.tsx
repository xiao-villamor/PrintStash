/** Root composition keeps public credential entries alive through their own verified transition. */
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { Route, Routes } from "react-router-dom";
import RootLayout from "@/root-layout";
import LoginPage from "@/pages/login";
import { getUser } from "@/lib/auth-store";
import { adminSession, json, renderApp } from "@/test-support/render";

describe("RootLayout entry boundary", () => {
  it("completes local sign-in through the real entry composition", async () => {
    const user = userEvent.setup();
    const me = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(
      <Routes>
        <Route element={<RootLayout />}>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<p>Verified private destination</p>} />
        </Route>
      </Routes>,
      {
        at: "/login",
        auth: adminSession({ user: null }),
        routes: {
          "GET /api/v1/setup/status": json({ configured: true, user_count: 1 }),
          "GET /api/v1/auth/providers": json({ oidc_enabled: false, oidc_display_name: "" }),
          "POST /api/v1/auth/login": json({ access_token: "cookie-token", token_type: "bearer" }),
          "GET /api/v1/auth/me": (_url, options) => {
            signal = options?.signal;
            return me.promise;
          },
        },
      },
    );
    await user.type(await screen.findByLabelText("Username"), "maker");
    await user.type(screen.getByLabelText("Password"), "password");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByLabelText("Username")).toHaveValue("maker");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();

    await act(async () =>
      me.resolve(
        json({
          id: 7,
          username: "maker",
          email: null,
          is_superuser: false,
          is_active: true,
          created_at: "2026-01-01T00:00:00Z",
          updated_at: "2026-01-01T00:00:00Z",
        }),
      ),
    );

    expect(await screen.findByText("Verified private destination")).toBeVisible();
    expect(getUser()?.username).toBe("maker");
    expect(signal).toBeDefined();
  });
});
