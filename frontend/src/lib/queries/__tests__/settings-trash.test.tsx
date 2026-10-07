/** Trash and GC owners publish receipts only while the initiating view retains authority. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { clearLogin, retirePrivateSessionScope } from "@/lib/auth-store";
import { getSessionVersion } from "@/lib/session-transport";
import {
  settingsTrashOptions,
  settingsGcOptions,
  settingsTrashKeys,
  useSettingsTrashCommand,
  type TrashCommand,
} from "@/lib/queries/settings-trash";
import { aTrashedModel, aGcPlan } from "@/test-support/factories";
import { json, renderApp, memberSession } from "@/test-support/render";

const row = aTrashedModel();
const plan = aGcPlan();
const reads = { "GET /api/v1/models/trash": json([row]), "GET /api/v1/admin/gc": json(plan) };
function Editor({ kind = "restore" }: { kind?: "restore" | "purge" | "abort" | "preview" }) {
  const rows = useQuery(settingsTrashOptions());
  const gc = useQuery(settingsGcOptions());
  const command = useSettingsTrashCommand();
  const [status, setStatus] = useState("Idle");
  async function save() {
    setStatus("Pending");
    const session = getSessionVersion();
    const intent: TrashCommand =
      kind === "restore"
        ? { kind, target: row, session }
        : kind === "purge"
          ? { kind, target: row, session, confirmStorageRisk: true }
          : kind === "abort"
            ? { kind, plan, session }
            : { kind, session };
    try {
      await command.run(intent);
      setStatus("Saved");
    } catch {
      setStatus("Failed");
    }
  }
  return (
    <>
      <p>{rows.data?.map((item) => item.name).join(",")}</p>
      <p>{gc.data?.state}</p>
      <p>{status}</p>
      <button onClick={() => void save()}>Save</button>
      <button onClick={() => void rows.refetch()}>Refresh</button>
    </>
  );
}

describe("Trash command ownership", () => {
  it.each(["restore", "purge"] as const)(
    "publishes confirmed %s without another listing",
    async (kind) => {
      const endpoint =
        kind === "restore"
          ? "POST /api/v1/models/7/restore"
          : "DELETE /api/v1/models/7/purge?confirm_storage_risk=true";
      const app = renderApp(<Editor kind={kind} />, {
        routes: {
          ...reads,
          [endpoint]: json(
            kind === "restore" ? { id: 7 } : { purged_model_ids: [7], purged_count: 1 },
          ),
        },
      });
      await screen.findByText("Old bracket");
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText("Saved");
      expect(app.client.getQueryData(settingsTrashKeys.rows)).toEqual([]);
      expect(app.requestsWithMethod("GET")).toHaveLength(2);
    },
  );
  it("prevents a held trash read from resurrecting a restored row", async () => {
    const held = Promise.withResolvers<Response>();
    const app = renderApp(<Editor />, {
      routes: { ...reads, "POST /api/v1/models/7/restore": json({ id: 7 }) },
    });
    await screen.findByText("Old bracket");
    app.route({ "GET /api/v1/models/trash": () => held.promise });
    await userEvent.click(screen.getByRole("button", { name: "Refresh" }));
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    await act(async () => held.resolve(json([row])));
    expect(app.client.getQueryData(settingsTrashKeys.rows)).toEqual([]);
    expect(screen.queryByText("Old bracket")).not.toBeInTheDocument();
  });
  it.each(["logout", "disposal"] as const)("retires a held receipt on %s", async (retirement) => {
    const held = Promise.withResolvers<Response>();
    const app = renderApp(<Editor />, {
      routes: { ...reads, "POST /api/v1/models/7/restore": () => held.promise },
    });
    await screen.findByText("Old bracket");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    app.unmount();
    if (retirement === "logout") clearLogin();
    const current = aTrashedModel({ name: "Current session row" });
    app.client.setQueryData(settingsTrashKeys.rows, [current]);
    await act(async () => held.resolve(json({ id: 7 })));
    expect(app.client.getQueryData(settingsTrashKeys.rows)).toEqual([current]);
  });
  it("keeps a replacement read alive after a disposed command", async () => {
    const held = Promise.withResolvers<Response>();
    const replacement = Promise.withResolvers<Response>();
    const app = renderApp(<Editor />, {
      routes: { ...reads, "POST /api/v1/models/7/restore": () => held.promise },
    });
    await screen.findByText("Old bracket");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    app.unmount();
    app.route({ "GET /api/v1/models/trash": () => replacement.promise });
    const reading = app.client.fetchQuery({ ...settingsTrashOptions(), staleTime: 0 });
    await act(async () => held.resolve(json({ id: 7 })));
    const current = aTrashedModel({ name: "Replacement view row" });
    await act(async () => replacement.resolve(json([current])));
    await expect(reading).resolves.toEqual([current]);
    expect(app.client.getQueryData(settingsTrashKeys.rows)).toEqual([current]);
  });
  it("refuses a stale gesture after permissions retire its scope", async () => {
    const app = renderApp(<Editor kind="abort" />, { routes: reads });
    await screen.findByText("preview");
    act(() => retirePrivateSessionScope());
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Failed");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("preserves a newer GC observation after a held receipt", async () => {
    const held = Promise.withResolvers<Response>();
    const current = aGcPlan({ id: 13, state: "quarantined" });
    const app = renderApp(<Editor kind="abort" />, {
      routes: { ...reads, "POST /api/v1/admin/gc/12/abort": () => held.promise },
    });
    await screen.findByText("preview");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    app.route({ "GET /api/v1/admin/gc": json(current) });
    act(() => app.client.setQueryData(settingsTrashKeys.plan, current));
    await act(async () => held.resolve(json(aGcPlan({ state: "aborted" }))));
    await screen.findByText("Saved");
    expect(app.client.getQueryData(settingsTrashKeys.plan)).toEqual(current);
    expect(screen.queryByText("aborted")).not.toBeInTheDocument();
  });
  it("refuses a GC command without administrator authority", async () => {
    const app = renderApp(<Editor kind="abort" />, { auth: memberSession(), routes: reads });
    await screen.findByText("preview");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Failed");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("refuses an intent after the current trash read fails", async () => {
    const app = renderApp(<Editor />, {
      routes: { ...reads, "GET /api/v1/models/trash": json({ detail: "forbidden" }, 403) },
    });
    await waitFor(() =>
      expect(app.client.getQueryState(settingsTrashKeys.rows)?.status).toBe("error"),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Failed");
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
});
