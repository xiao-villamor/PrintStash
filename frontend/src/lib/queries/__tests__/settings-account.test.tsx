/** Account commands publish only current nonsecret authoritative projections. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  accountKeys,
  apiKeysOptions,
  adminUsersOptions,
  useApiKeyCommand,
  useAdminUserCommand,
  type ApiKeyReceipt,
} from "@/lib/queries/settings-account";
import { getSessionVersion } from "@/lib/session-transport";
import { clearLogin } from "@/lib/auth-store";
import { aApiKey, aUser } from "@/test-support/account";
import { json, renderApp } from "@/test-support/render";
import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  prepareBrowserExtensionSetup,
} from "@/lib/browser-extension-setup";

function KeyEditor({ deliver }: { deliver?: (receipt: ApiKeyReceipt) => void }) {
  const query = useQuery(apiKeysOptions(1));
  const command = useApiKeyCommand();
  const [receipt, setReceipt] = useState<string | null>(null);
  return (
    <>
      {query.data?.map((key) => (
        <p key={key.id}>{key.name}</p>
      ))}
      <p>{receipt}</p>
      <button
        onClick={() =>
          command.mutate({
            kind: "create",
            name: "Slicer",
            userId: 1,
            session: getSessionVersion(),
            receiveReceipt: (result) => {
              setReceipt(result.secret);
              deliver?.(result);
            },
          })
        }
      >
        Mint
      </button>
      <button
        onClick={() =>
          command.mutate({ kind: "revoke", id: 9, userId: 1, session: getSessionVersion() })
        }
      >
        Revoke
      </button>
    </>
  );
}
function UserEditor() {
  const query = useQuery(adminUsersOptions(1));
  const command = useAdminUserCommand();
  return (
    <>
      {query.data?.map((user) => (
        <p key={user.id}>
          {user.username} {user.is_active ? "Active" : "Disabled"}
        </p>
      ))}
      <button
        onClick={() =>
          command.mutate({
            kind: "create",
            payload: { username: "maker", password: "FakePassword123" },
            userId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Create User
      </button>
      <button
        onClick={() =>
          command.mutate({
            kind: "update",
            id: 2,
            payload: { is_superuser: true },
            userId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Update User
      </button>
      <button
        onClick={() =>
          command.mutate({
            kind: "password",
            id: 2,
            password: "FakePassword123",
            userId: 1,
            session: getSessionVersion(),
          })
        }
      >
        Reset password
      </button>
      <button
        onClick={() =>
          command.mutate({ kind: "deactivate", id: 2, userId: 1, session: getSessionVersion() })
        }
      >
        Deactivate User
      </button>
    </>
  );
}
const secret = "ps_test_this-is-not-a-real-key";
function renderKeys(ui = <KeyEditor />) {
  return renderApp(ui, {
    routes: {
      "GET /api/v1/auth/api-keys": json([]),
      "POST /api/v1/auth/api-keys": json({ ...aApiKey(), api_key: secret }),
    },
  });
}
afterEach(() => {
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("account snapshot ownership", () => {
  it("keeps an issued secret outside shared caches", async () => {
    const app = renderKeys();
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Mint" }));
    expect(await screen.findByText(secret)).toBeVisible();
    expect(screen.getByText("Slicer")).toBeVisible();
    expect(app.client.getQueryData(accountKeys.apiKeys(1))).toEqual([aApiKey()]);
    const cache = JSON.stringify({
      queries: app.client
        .getQueryCache()
        .getAll()
        .map((query) => query.state.data),
      mutations: app.client
        .getMutationCache()
        .getAll()
        .map((mutation) => mutation.state),
    });
    expect(cache).not.toContain(secret);
    expect(cache).not.toContain("api_key");
    expect(app.requests().filter((call) => call.method === "GET")).toHaveLength(1);
  });
  it("removes a revoked key after the acknowledged command", async () => {
    const app = renderApp(<KeyEditor />, {
      routes: {
        "GET /api/v1/auth/api-keys": json([aApiKey()]),
        "DELETE /api/v1/auth/api-keys/9": json(null, 204),
      },
    });
    await screen.findByText("Slicer");
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
    await waitFor(() => expect(screen.queryByText("Slicer")).toBeNull());
    expect(app.client.getQueryData(accountKeys.apiKeys(1))).toEqual([]);
    expect(app.requests().filter((call) => call.method === "GET")).toHaveLength(1);
  });
  it.each([
    { label: "create", button: "Create User", method: "POST", path: "/api/v1/admin/users" },
    { label: "update", button: "Update User", method: "PATCH", path: "/api/v1/admin/users/2" },
    {
      label: "password",
      button: "Reset password",
      method: "POST",
      path: "/api/v1/admin/users/2/password",
    },
  ])(
    "publishes the authoritative $label User DTO without a duplicate read",
    async ({ button, method, path }) => {
      const updated = aUser({ username: "Normalized server user" });
      const app = renderApp(<UserEditor />, {
        routes: {
          "GET /api/v1/admin/users": json([aUser()]),
          [`${method} ${path}`]: json(updated),
        },
      });
      await screen.findByText("maker Active");
      await userEvent.click(screen.getByRole("button", { name: button }));
      expect(await screen.findByText("Normalized server user Active")).toBeVisible();
      expect(app.client.getQueryData(accountKeys.users(1))).toEqual([updated]);
      expect(app.requests().filter((call) => call.method === "GET")).toHaveLength(1);
    },
  );
  it("refreshes a deactivated User from the authoritative list", async () => {
    let reads = 0;
    const app = renderApp(<UserEditor />, {
      routes: {
        "GET /api/v1/admin/users": () => json([aUser({ is_active: ++reads === 1 })]),
        "DELETE /api/v1/admin/users/2": json(null, 204),
      },
    });
    await screen.findByText("maker Active");
    await userEvent.click(screen.getByRole("button", { name: "Deactivate User" }));
    expect(await screen.findByText("maker Disabled")).toBeVisible();
    expect(reads).toBe(2);
    expect(app.requestsWithMethod("DELETE")).toHaveLength(1);
  });
  it("never dispatches a retired account gesture", async () => {
    const app = renderKeys();
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Mint" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.client.getQueryData(accountKeys.apiKeys(1))).toBeUndefined();
  });
  it("discards a key acknowledgement after delayed publication retires", async () => {
    const app = renderKeys();
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    const paused = Promise.withResolvers<void>();
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused.promise);
    await userEvent.click(screen.getByRole("button", { name: "Mint" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      paused.resolve();
      await paused.promise;
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(app.client.getQueryData(accountKeys.apiKeys(1))).toBeUndefined();
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toBeNull();
  });
  it("never dispatches a retired User gesture", async () => {
    const app = renderApp(<UserEditor />, {
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "POST /api/v1/admin/users": json(aUser()),
      },
    });
    await screen.findByText("maker Active");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Create User" }));
      app.unmount();
      clearLogin();
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
    expect(app.client.getQueryData(accountKeys.users(1))).toBeUndefined();
  });
  it("discards a User acknowledgement after delayed publication retires", async () => {
    const app = renderApp(<UserEditor />, {
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "POST /api/v1/admin/users": json(aUser({ username: "retired" })),
      },
    });
    await screen.findByText("maker Active");
    const paused = Promise.withResolvers<void>();
    const cancel = app.client.cancelQueries.bind(app.client);
    const spy = vi
      .spyOn(app.client, "cancelQueries")
      .mockImplementationOnce(cancel)
      .mockImplementationOnce(() => paused.promise);
    await userEvent.click(screen.getByRole("button", { name: "Create User" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    await act(async () => {
      app.unmount();
      clearLogin();
      paused.resolve();
      await paused.promise;
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
    expect(app.client.getQueryData(accountKeys.users(1))).toBeUndefined();
  });
  it("never delivers an issued receipt to a disposed view", async () => {
    const response = Promise.withResolvers<Response>();
    const app = renderKeys(
      <KeyEditor
        deliver={(receipt) =>
          prepareBrowserExtensionSetup("http://localhost:3000", "admin", receipt.secret)
        }
      />,
    );
    app.route({ "POST /api/v1/auth/api-keys": () => response.promise });
    await waitFor(() => expect(app.requests()).toHaveLength(1));
    await userEvent.click(screen.getByRole("button", { name: "Mint" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    app.unmount();
    await act(async () => {
      response.resolve(json({ ...aApiKey(), api_key: secret }));
    });
    await waitFor(() =>
      expect(app.client.getMutationCache().getAll()[0]?.state.status).toBe("success"),
    );
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toBeNull();
  });
});
