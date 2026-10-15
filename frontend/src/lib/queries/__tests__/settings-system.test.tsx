/** System status readers share Query ownership instead of effect-local copies. */
import { QueryClient, useQuery } from "@tanstack/react-query";
import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryClient } from "@/lib/query-client";
import {
  settingsComputeOptions,
  settingsHealthOptions,
  settingsReleaseOptions,
} from "@/lib/queries/settings-system";
import { json, renderApp } from "@/test-support/render";
// Captured before any harness render replaces them with test defaults.
const productionDefaults = queryClient.getDefaultOptions();
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
  describe("server GPU", () => {
    afterEach(() => {
      vi.useRealTimers();
      vi.unstubAllGlobals();
    });
    it("keeps a GPU answer nobody is viewing until someone checks again", async () => {
      vi.useFakeTimers({ toFake: ["Date", "setTimeout", "clearTimeout"] });
      const fetchMock = vi.fn<typeof fetch>(async () => json({ capabilities: [] }));
      vi.stubGlobal("fetch", fetchMock);
      const client = new QueryClient({ defaultOptions: productionDefaults });
      await client.fetchQuery(settingsComputeOptions());
      vi.advanceTimersByTime(60 * 60_000);

      await client.fetchQuery(settingsComputeOptions());
      expect(fetchMock).toHaveBeenCalledTimes(1);
      await client.refetchQueries({ queryKey: settingsComputeOptions().queryKey });
      expect(fetchMock).toHaveBeenCalledTimes(2);
      client.clear();
    });
  });
});
