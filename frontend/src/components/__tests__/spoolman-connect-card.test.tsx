/*
 * Wiring the vault to a Spoolman instance somebody else is running.
 *
 * The API key is the piece that decides the shape of this file. It is stored,
 * never read back, and shown as a mask — so leaving the field untouched has to
 * mean "keep what you have". Sending the mask replaces a working key with eight
 * asterisks, and the failure surfaces later as a spool that will not update.
 *
 * "Test connection" checks what is *typed*, not what is saved. Testing the
 * stored config would make it useless for the one job it has: proving a URL
 * before committing it.
 *
 * Write-back is separately switchable because it is the only part that changes
 * data in the other system. And Moonraker can already be decrementing the same
 * spool through its own hook, so writing again double-counts every gram — the
 * card has to say so rather than quietly halving somebody's inventory.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SpoolmanConnectCard } from "@/components/spoolman-connect-card";
import { clearLogin } from "@/lib/auth-store";
import { queryKeys } from "@/lib/query-client";
import { json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { SpoolmanStatus } from "@/types";

function aStatus(over: Partial<SpoolmanStatus> = {}): SpoolmanStatus {
  return {
    edit_epoch: "a".repeat(32),
    edit_version: 1,
    enabled: true,
    base_url: "http://spoolman.test:7912",
    has_api_key: true,
    write_enabled: false,
    write_force: false,
    connected: true,
    version: "0.18.0",
    error: null,
    native_hook_detected: false,
    ...over,
  };
}

function renderCard(
  options: RenderAppOptions & { status?: SpoolmanStatus; canEdit?: boolean } = {},
) {
  const { status = aStatus(), canEdit = true, seed = [], routes = {}, ...rest } = options;
  return renderApp(<SpoolmanConnectCard canEdit={canEdit} />, {
    seed: [[queryKeys.spoolmanStatus, status], [queryKeys.spools, []], ...seed],
    routes: {
      "GET /api/v1/spoolman": json(status),
      "GET /api/v1/spoolman/spools": json([]),
      "PUT /api/v1/spoolman": json({ ...status, edit_version: status.edit_version + 1 }),
      "POST /api/v1/spoolman/test": json({ connected: true, version: "0.18.0", error: null }),
      ...routes,
    },
    ...rest,
  });
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("SpoolmanConnectCard", () => {
  describe("what it shows", () => {
    it("preserves a connection draft on status refresh", async () => {
      const { client } = renderCard();
      const input = await screen.findByDisplayValue("http://spoolman.test:7912");
      await userEvent.clear(input);
      await userEvent.type(input, "http://draft.test:7912");
      act(() => client.setQueryData(queryKeys.spoolmanStatus, aStatus({ connected: false })));
      await screen.findByText("Not connected");
      await waitFor(() => expect(input).toHaveValue("http://draft.test:7912"));
    });
    it("retains a rejected connection draft", async () => {
      renderCard({ routes: { "PUT /api/v1/spoolman": json({ detail: "edit_conflict" }, 412) } });
      const input = await screen.findByDisplayValue("http://spoolman.test:7912");
      await userEvent.clear(input);
      await userEvent.type(input, "http://draft.test:7912");
      await userEvent.click(screen.getByRole("button", { name: /Save/ }));
      expect(await screen.findByRole("button", { name: /Review latest/ })).toBeVisible();
      expect(input).toHaveValue("http://draft.test:7912");
      expect(screen.getByRole("button", { name: /^Save$/ })).toBeDisabled();
    });

    it("fills the form from the saved configuration", async () => {
      renderCard();

      expect(await screen.findByDisplayValue("http://spoolman.test:7912")).toBeInTheDocument();
    });

    it("shows a stored key as a mask rather than as itself", async () => {
      // The server never returns it; rendering an empty field would suggest
      // there is no key at all.
      renderCard();

      expect(await screen.findByDisplayValue("********")).toBeInTheDocument();
    });

    it("leaves the key field blank when none is stored", async () => {
      renderCard({ status: aStatus({ has_api_key: false }) });

      await screen.findByDisplayValue("http://spoolman.test:7912");
      expect(screen.queryByDisplayValue("********")).toBeNull();
    });

    it("tells a non-administrator who can configure it", async () => {
      renderCard({ canEdit: false });

      expect(
        await screen.findByText("Only an administrator can configure Spoolman."),
      ).toBeInTheDocument();
    });
  });

  describe("editing recovery", () => {
    it("blocks editing without an initial status", async () => {
      const app = renderApp(<SpoolmanConnectCard canEdit />, {
        routes: { "GET /api/v1/spoolman": json({ detail: "unavailable" }, 503) },
      });
      await screen.findByRole("alert");
      expect(screen.queryByRole("button", { name: /^Save$/ })).not.toBeInTheDocument();
      app.route({ "GET /api/v1/spoolman": json(aStatus()) });
      await userEvent.click(screen.getByRole("button", { name: "Retry" }));
      expect(await screen.findByDisplayValue("http://spoolman.test:7912")).toBeVisible();
    });
    it("requires explicit revised save after reviewing a conflict", async () => {
      const app = renderCard({
        routes: { "PUT /api/v1/spoolman": json({ detail: "edit_conflict" }, 412) },
      });
      const input = await screen.findByDisplayValue("http://spoolman.test:7912");
      await userEvent.clear(input);
      await userEvent.type(input, "http://draft.test:7912");
      await userEvent.click(screen.getByRole("button", { name: /^Save$/ }));
      app.route({
        "GET /api/v1/spoolman": json(
          aStatus({ edit_version: 4, base_url: "http://latest.test:7912" }),
        ),
      });
      await userEvent.click(await screen.findByRole("button", { name: /Review latest/ }));
      await screen.findByText("http://latest.test:7912");
      expect(input).toHaveValue("http://draft.test:7912");
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
      let receivedBase: string | null = null;
      app.route({
        "PUT /api/v1/spoolman": (_url, init) => {
          receivedBase = new Headers(init?.headers).get("If-Match");
          return json(aStatus({ edit_version: 5, base_url: "http://draft.test:7912" }));
        },
      });
      await userEvent.click(
        screen.getByRole("button", { name: "Save my draft against this version" }),
      );
      await screen.findByText("Saved.");
      expect(app.requestsWithMethod("PUT")).toHaveLength(2);
      expect(receivedBase).toBe(`"spoolman-settings-e${"a".repeat(32)}-v4"`);
    });
    it("retires a pending connection command", async () => {
      const pending = Promise.withResolvers<Response>();
      const app = renderCard({ routes: { "PUT /api/v1/spoolman": () => pending.promise } });
      await userEvent.click(screen.getByRole("button", { name: /^Save$/ }));
      await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
      await act(async () => {
        clearLogin();
        app.client.setQueryData(
          queryKeys.spoolmanStatus,
          aStatus({ base_url: "http://replacement.test:7912" }),
        );
        pending.resolve(json(aStatus({ edit_version: 2, base_url: "http://retired.test:7912" })));
      });
      expect(app.client.getQueryData(queryKeys.spoolmanStatus)).toMatchObject({
        base_url: "http://replacement.test:7912",
      });
      expect(screen.queryByText("Saved.")).not.toBeInTheDocument();
      expect(app.client.getMutationCache().getAll()).toHaveLength(0);
    });
  });
  describe("saving the connection", () => {
    it("revalidates spools after changing the connection", async () => {
      renderCard({
        seed: [[queryKeys.spools, []]],
        routes: {
          "GET /api/v1/spoolman/spools": json([
            {
              id: 7,
              filament_name: "Replacement PETG",
              vendor_name: "Test",
              remaining_weight: 800,
            },
          ]),
        },
      });
      await userEvent.click(screen.getByRole("button", { name: /Save/ }));
      expect(await screen.findByText(/Replacement PETG/)).toBeVisible();
    });

    it("sends the URL the operator typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const url = await screen.findByDisplayValue("http://spoolman.test:7912");
      await user.clear(url);
      await user.type(url, "http://nas.local:7912");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          base_url: "http://nas.local:7912",
        }),
      );
    });

    it("keeps the stored key when the field is untouched", async () => {
      // Sending the mask replaces a working key with eight asterisks, and the
      // failure only surfaces later as a spool that will not update.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByDisplayValue("********");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).not.toHaveProperty(
          "api_key",
        ),
      );
    });

    it("sends a new key when one is typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const key = await screen.findByDisplayValue("********");
      await user.clear(key);
      await user.type(key, "not-a-real-key");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          api_key: "not-a-real-key",
        }),
      );
    });

    it("will not save without a URL", async () => {
      renderCard({ status: aStatus({ base_url: null }) });

      await screen.findByRole("button", { name: /Save/ });

      expect(screen.getByRole("button", { name: /Save/ })).toBeDisabled();
    });

    it("surfaces a configuration the server refused", async () => {
      const user = userEvent.setup();
      renderCard({
        routes: { "PUT /api/v1/spoolman": json({ detail: "invalid_url" }, 422) },
      });
      await screen.findByDisplayValue("http://spoolman.test:7912");

      await user.click(screen.getByRole("button", { name: /Save/ }));

      expect(await screen.findByText("Invalid URL.")).toBeInTheDocument();
    });
  });

  describe("testing the connection", () => {
    it("tests what is typed rather than what is saved", async () => {
      // Testing the stored config would make the button useless for its one
      // job: proving a URL before committing it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const url = await screen.findByDisplayValue("http://spoolman.test:7912");
      await user.clear(url);
      await user.type(url, "http://nas.local:7912");

      await user.click(screen.getByRole("button", { name: /Test connection/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          base_url: "http://nas.local:7912",
        }),
      );
    });

    it("reports the version it reached", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByDisplayValue("http://spoolman.test:7912");

      await user.click(screen.getByRole("button", { name: /Test connection/ }));

      expect(await screen.findByText("Connected — Spoolman v0.18.0.")).toBeInTheDocument();
    });

    it("reports a Spoolman that answered but refused", async () => {
      // A 200 carrying `connected: false` is a different failure from an
      // unreachable host, and only Spoolman knows which.
      const user = userEvent.setup();
      renderCard({
        routes: {
          "POST /api/v1/spoolman/test": json({
            connected: false,
            version: null,
            error: "401 Unauthorized",
          }),
        },
      });
      await screen.findByDisplayValue("http://spoolman.test:7912");

      await user.click(screen.getByRole("button", { name: /Test connection/ }));

      expect(await screen.findByText("401 Unauthorized")).toBeInTheDocument();
    });
  });

  describe("turning it on and off", () => {
    it("enables the integration", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({ status: aStatus({ enabled: false }) });

      await user.click(await screen.findByRole("checkbox", { name: /Enable Spoolman/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          enabled: true,
        }),
      );
    });

    it("hides write-back while the integration is off", async () => {
      // Writing to a Spoolman the vault is not reading from cannot do anything
      // except confuse the operator.
      renderCard({ status: aStatus({ enabled: false }) });

      await screen.findByRole("checkbox", { name: /Enable Spoolman/ });
      expect(screen.queryByRole("checkbox", { name: /Write consumption/ })).toBeNull();
    });

    it("turns write-back on separately", async () => {
      // It is the only part that changes data in the other system.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();

      await user.click(await screen.findByRole("checkbox", { name: /Write consumption/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          write_enabled: true,
        }),
      );
    });
  });

  describe("the inventory", () => {
    it("lists the spools Spoolman is tracking", async () => {
      renderCard({
        seed: [
          [
            queryKeys.spools,
            [{ id: 7, filament_name: "PETG black", vendor_name: "Prusa", remaining_weight: 800 }],
          ],
        ],
        routes: {
          "GET /api/v1/spoolman/spools": json([
            { id: 7, filament_name: "PETG black", vendor_name: "Prusa", remaining_weight: 800 },
          ]),
        },
      });

      expect(await screen.findByText(/PETG black/)).toBeInTheDocument();
    });

    it("shows how much is left on each", async () => {
      // The number is the reason to look: a spool with 40 g left will not
      // finish the print somebody is about to send.
      renderCard({
        seed: [[queryKeys.spools, [{ id: 7, filament_name: "PETG black", remaining_weight: 800 }]]],
        routes: {
          "GET /api/v1/spoolman/spools": json([
            { id: 7, filament_name: "PETG black", remaining_weight: 800 },
          ]),
        },
      });

      expect(await screen.findByText(/800g left/)).toBeInTheDocument();
    });

    it("lists nothing while the integration is off", async () => {
      renderCard({
        status: aStatus({ enabled: false }),
        seed: [[queryKeys.spools, [{ id: 7, filament_name: "PETG black", remaining_weight: 800 }]]],
      });

      await screen.findByRole("checkbox", { name: /Enable Spoolman/ });
      expect(screen.queryByText(/PETG black/)).toBeNull();
    });
  });
});
