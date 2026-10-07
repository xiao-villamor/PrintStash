/** System status readers share Query ownership instead of effect-local copies. */
import { useQuery } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { settingsHealthOptions, settingsReleaseOptions } from "@/lib/queries/settings-system";
import { json, renderApp } from "@/test-support/render";
function Reader() {
  const health = useQuery(settingsHealthOptions());
  const release = useQuery(settingsReleaseOptions());
  return (
    <p>
      {health.data?.name}/{release.data?.current_version}
    </p>
  );
}
describe("Settings system owner", () => {
  it("shares system status between observers", async () => {
    const app = renderApp(
      <>
        <Reader />
        <Reader />
      </>,
      {
        routes: {
          "GET /api/v1/health/details": json({ name: "PrintStash" }),
          "GET /api/v1/health/releases/latest": json({ current_version: "0.14.0" }),
        },
      },
    );
    expect(await screen.findAllByText("PrintStash/0.14.0")).toHaveLength(2);
    expect(app.requestsWithMethod("GET")).toHaveLength(2);
  });
});
