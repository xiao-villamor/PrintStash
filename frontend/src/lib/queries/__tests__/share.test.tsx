/** Public readers share only the capability token they were given. */
import { useQuery } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { sharedModelOptions } from "@/lib/queries/share";
import { json, renderApp } from "@/test-support/render";

function Name({ token }: { token: string }) {
  const query = useQuery(sharedModelOptions(token));
  return <output>{query.data?.name}</output>;
}

describe("sharedModelOptions", () => {
  it("shares one current token read between observers", async () => {
    const app = renderApp(
      <>
        <Name token="abc" />
        <Name token="abc" />
        <Name token="other" />
      </>,
      {
        routes: {
          "GET /api/v1/share/abc": json({
            name: "Public boat",
            description: null,
            has_thumbnail: false,
            allow_download: false,
            files: [],
          }),
          "GET /api/v1/share/other": json({
            name: "Other fixture",
            description: null,
            has_thumbnail: false,
            allow_download: false,
            files: [],
          }),
        },
      },
    );
    expect(await screen.findAllByText("Public boat")).toHaveLength(2);
    expect(await screen.findByText("Other fixture")).toBeVisible();
    expect(app.requests().filter(({ url }) => url.endsWith("/share/abc"))).toHaveLength(1);
    expect(app.requests().filter(({ url }) => url.endsWith("/share/other"))).toHaveLength(1);
  });
});
