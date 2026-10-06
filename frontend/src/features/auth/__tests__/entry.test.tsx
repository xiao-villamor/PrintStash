/** The Login methods snapshot has one Query owner across mounted consumers. */
import { screen } from "@testing-library/react";
import { useQuery } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { loginProvidersOptions } from "@/features/auth/entry";
import { adminSession, json, renderApp } from "@/test-support/render";

function LoginMethods() {
  const methods = useQuery(loginProvidersOptions());
  return <output>{methods.data?.oidc_display_name}</output>;
}

describe("login methods ownership", () => {
  it("shares login methods between mounted entries", async () => {
    const view = renderApp(
      <>
        <LoginMethods />
        <LoginMethods />
      </>,
      {
        auth: adminSession({ user: null }),
        routes: {
          "GET /api/v1/auth/providers": json({
            oidc_enabled: true,
            oidc_display_name: "Authentik",
          }),
        },
      },
    );

    expect(await screen.findAllByText("Authentik")).toHaveLength(2);

    expect(view.requestsWithMethod("GET")).toEqual([
      { method: "GET", url: "/api/v1/auth/providers", body: "" },
    ]);
  });
});
