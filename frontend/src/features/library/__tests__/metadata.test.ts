/** Library metadata refresh preserves acknowledged details and navigation snapshots. */
import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";
import { refreshLibraryMetadata } from "../metadata";
import { queryKeys } from "@/lib/query-client";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
describe("library metadata publication", () => {
  it.each(["model", "multipart"] as const)(
    "preserves an acknowledged %s while refreshing derived metadata",
    (kind) => {
      const client = new QueryClient();
      const exact = kind === "model" ? queryKeys.model(7) : queryKeys.multipartModel(7);
      client.setQueryData(exact, { name: "Confirmed" });
      client.setQueryData(queryKeys.vaultStats, { count: 1 });
      client.setQueryData(["library-browse", "snapshot"], { rows: [7] });
      refreshLibraryMetadata(client, getSessionVersion(), { kind, id: 7 });
      expect(client.getQueryState(exact)?.isInvalidated).toBe(false);
      expect(client.getQueryState(queryKeys.vaultStats)?.isInvalidated).toBe(true);
      expect(client.getQueryState(["library-browse", "snapshot"])?.isInvalidated).toBe(false);
    },
  );
  it("leaves replacement-session metadata unchanged", () => {
    const client = new QueryClient();
    const session = getSessionVersion();
    clearLogin();
    client.setQueryData(queryKeys.vaultStats, { count: 2 });
    refreshLibraryMetadata(client, session);
    expect(client.getQueryState(queryKeys.vaultStats)?.isInvalidated).toBe(false);
  });
});
