/**
 * Remote storage profiles are configured once and consumed by two independent
 * workflows. These tests defend the usage assignment and write-only credential
 * boundary so moving the form out of Backup and Library sources cannot silently
 * narrow a shared profile or send a secret back in a later update.
 */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RemoteStorageConnections } from "@/components/remote-storage-connections";
import { clearLogin } from "@/lib/auth-store";
import { AuthContext } from "@/lib/auth-context";
import { storageProviderCatalogue } from "@/test-support/storage-provider-catalogue";
import { aStorageConnection } from "@/test-support/factories";
import { adminSession, memberSession, json, renderApp } from "@/test-support/render";

describe("RemoteStorageConnections", () => {
  it("explains disabled transport selection", async () => {
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/storage/providers": json([
          {
            ...storageProviderCatalogue.find((provider) => provider.id === "s3"),
            uses: {
              vault: { available: true },
              library: { available: false, reason: "storage_service_not_compiled" },
              backup: { available: false, reason: "storage_service_not_compiled" },
            },
          },
        ]),
      },
    });
    expect(
      await screen.findByText("This API image does not include the required storage service."),
    ).toBeVisible();
    expect(screen.getByRole("button", { name: "Amazon S3 or compatible" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save connection" })).toBeDisabled();
    expect(view.requestsWithMethod("POST")).toHaveLength(0);
  });

  it("keeps existing profiles visible when the provider catalogue fails", async () => {
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json({ detail: "unavailable" }, 503),
      },
    });
    expect(await screen.findByText("Workshop storage")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save connection" })).toBeDisabled();
  });

  it("lists each remote profile with its current uses", async () => {
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([
          aStorageConnection(),
          aStorageConnection({ id: 2, name: "Archive only", purpose: "backup" }),
        ]),
      },
    });

    expect(await screen.findByText("Workshop storage")).toBeVisible();
    expect(screen.getByRole("combobox", { name: "Use Workshop storage for" })).toHaveValue("both");
    expect(screen.getByRole("combobox", { name: "Use Archive only for" })).toHaveValue("backup");
  });

  it("holds the connection form in a skeleton until providers load", async () => {
    let finish: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": () => pending,
        "GET /api/v1/storage-connections": json([]),
      },
    });

    expect(screen.getByRole("status", { name: "Add remote connection" })).toBeVisible();
    expect(screen.queryByLabelText("Connection name")).toBeNull();
    finish(json(storageProviderCatalogue));
    expect(await screen.findByLabelText("Connection name")).toBeVisible();
    expect(screen.queryByRole("status", { name: "Add remote connection" })).toBeNull();
  });

  it("selects remote providers by category", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([]),
      },
    });
    const categories = await screen.findByRole("group", { name: "Storage category" });

    await user.click(within(categories).getByRole("button", { name: "Nextcloud and WebDAV" }));

    expect(
      within(categories).getByRole("button", { name: "Nextcloud and WebDAV" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: /^Nextcloud$/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.getByLabelText("Server URL")).toBeVisible();
    expect(screen.queryByLabelText("Bucket")).toBeNull();
  });

  it("creates a Google Drive profile for both workflows", async () => {
    const user = userEvent.setup();
    const created = aStorageConnection({
      id: 9,
      name: "Shared Drive",
      kind: "gdrive",
      configuration: { client_id: "google-client", root: "PrintStash" },
      secret_fields_set: ["client_secret", "refresh_token"],
    });
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([]),
        "POST /api/v1/storage-connections": json(created, 201),
      },
    });
    await screen.findByText(/No remote storage connected yet/);

    await user.click(screen.getByLabelText("Connection name"));
    await user.paste("Shared Drive");
    await user.click(
      within(screen.getByRole("group", { name: "Storage category" })).getByRole("button", {
        name: "Google Drive",
      }),
    );
    await user.click(screen.getByLabelText("OAuth client ID"));
    await user.paste("google-client");
    await user.click(screen.getByLabelText("OAuth client secret"));
    await user.paste("google-secret");
    await user.click(screen.getByLabelText("Refresh token"));
    await user.paste("google-refresh");
    await user.click(screen.getByRole("button", { name: "Backups + libraries" }));
    await user.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0].body)).toEqual({
      name: "Shared Drive",
      kind: "gdrive",
      purpose: "both",
      configuration: { provider: "gdrive", client_id: "google-client", root: "PrintStash" },
      secrets: { client_secret: "google-secret", refresh_token: "google-refresh" },
    });
    expect(await screen.findByText("Shared Drive")).toBeVisible();
    expect(screen.queryByDisplayValue("google-secret")).toBeNull();
  });

  it("changes which workflows may reuse a connection", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(
          aStorageConnection({ purpose: "library", edit_version: 2 }),
        ),
      },
    });
    const usage = await screen.findByRole("combobox", { name: "Use Workshop storage for" });

    await user.selectOptions(usage, "library");

    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body)).toEqual({
      purpose: "library",
    });
    expect(usage).toHaveValue("library");
  });

  it("returns to Library-only use after editing a shared connection", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
      },
    });
    await screen.findByText("Workshop storage");

    await user.click(screen.getByRole("button", { name: "Edit" }));
    expect(screen.getByRole("button", { name: "Backups + libraries" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    await user.click(screen.getByRole("button", { name: "Cancel editing" }));

    expect(screen.getByRole("button", { name: "Library sources" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("pauses a connection without changing its uses", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(
          aStorageConnection({ enabled: false, edit_version: 2 }),
        ),
      },
    });
    await screen.findByText("Workshop storage");

    await user.click(screen.getByRole("button", { name: "Pause" }));

    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body)).toEqual({ enabled: false });
    expect(screen.getByRole("combobox", { name: "Use Workshop storage for" })).toHaveValue("both");
  });

  it("explains unavailable Google Drive support", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([
          aStorageConnection({ kind: "gdrive", name: "Recovery Drive" }),
        ]),
        "POST /api/v1/storage-connections/1/probe": json(
          { detail: "gdrive_transport_unavailable" },
          409,
        ),
      },
    });
    await screen.findByText("Recovery Drive");

    await user.click(screen.getByRole("button", { name: "Test" }));

    expect(
      await screen.findByText(
        "Google Drive isn't available in this server image. Upgrade or rebuild the full image, then try again.",
      ),
    ).toBeVisible();
  });

  it("explains a remote library listing failure", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([
          aStorageConnection({ kind: "sftp", name: "Workshop SFTP", purpose: "both" }),
        ]),
        "POST /api/v1/storage-connections/1/probe": json(
          { detail: "remote_storage_list_failed" },
          409,
        ),
      },
    });
    await screen.findByText("Workshop SFTP");

    await user.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByText(/could not list the remote folder/i)).toBeVisible();
  });

  it("explains that a shared profile can list the Library but cannot probe backups", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([
          aStorageConnection({ kind: "sftp", name: "Workshop SFTP", purpose: "both" }),
        ]),
        "POST /api/v1/storage-connections/1/probe": json(
          { detail: "storage_connection_backup_probe_failed" },
          409,
        ),
      },
    });
    await screen.findByText("Workshop SFTP");

    await user.click(screen.getByRole("button", { name: "Test" }));

    expect(await screen.findByText(/Library listing succeeded/i)).toBeVisible();
  });

  it("keeps save unavailable until the profile has a name", async () => {
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([]),
      },
    });

    expect(await screen.findByRole("button", { name: "Save connection" })).toBeDisabled();
  });

  it("keeps save unavailable until required provider credentials are complete", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([]),
      },
    });
    const save = await screen.findByRole("button", { name: "Save connection" });

    await user.type(screen.getByLabelText("Connection name"), "Incomplete S3");
    await user.type(screen.getByLabelText("Bucket"), "models");

    expect(save).toBeDisabled();
  });
  it("preserves omitted credentials when editing a connection", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(
          aStorageConnection({ name: "Renamed", edit_version: 2 }),
        ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByLabelText("Secret key")).toHaveValue("");
    await user.clear(screen.getByLabelText("Connection name"));
    await user.type(screen.getByLabelText("Connection name"), "Renamed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body).secrets).toEqual({});
    expect(await screen.findByText("Renamed")).toBeVisible();
  });

  it("submits only the explicitly replaced credential", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ edit_version: 2 })),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    await user.type(screen.getByLabelText("Secret key"), "replacement");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(view.requestsWithMethod("PATCH")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body).secrets).toEqual({
      secret_key: "replacement",
    });
    expect(screen.queryByDisplayValue("replacement")).not.toBeInTheDocument();
  });

  it("keeps edits visible when a dependent target change is refused", async () => {
    const user = userEvent.setup();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(
          { detail: "storage_connection_target_in_use" },
          409,
        ),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    await user.clear(screen.getByLabelText("Bucket"));
    await user.type(screen.getByLabelText("Bucket"), "different-bucket");
    await user.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    expect(screen.getByLabelText("Bucket")).toHaveValue("different-bucket");
    await user.click(screen.getByRole("button", { name: "Cancel editing" }));
    expect(
      screen.queryByRole("heading", { name: "Edit Workshop storage" }),
    ).not.toBeInTheDocument();
  });
});

describe("RemoteStorageConnections preset selection", () => {
  it("excludes mounted NAS presets from remote profiles", async () => {
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      },
    });
    const categories = await screen.findByRole("group", { name: "Storage category" });
    await userEvent
      .setup()
      .click(within(categories).getByRole("button", { name: "Nextcloud and WebDAV" }));
    expect(screen.getByRole("button", { name: "Koofr — WebDAV" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "TrueNAS — mounted folder" })).toBeNull();
    expect(within(categories).getByRole("button", { name: "Google Drive" })).toBeEnabled();
  });
  it("saves the Storage Box preset with its SFTP transport", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "POST /api/v1/storage-connections": json(
          aStorageConnection({ name: "Offsite box", kind: "sftp" }),
          201,
        ),
      },
    });
    const categories = await screen.findByRole("group", { name: "Storage category" });
    await user.click(within(categories).getByRole("button", { name: "NAS over SFTP" }));
    await user.click(screen.getByRole("button", { name: "Hetzner Storage Box — SFTP" }));
    await user.type(screen.getByLabelText("Connection name"), "Offsite box");
    await user.type(screen.getByLabelText("Host"), "box.example.test");
    await user.type(screen.getByLabelText("Username"), "owner");
    await user.type(screen.getByLabelText("Host key"), "/run/known_hosts");
    await user.type(screen.getByLabelText(/^Password/), "test-password");
    await user.click(screen.getByRole("button", { name: "Save connection" }));
    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0].body)).toMatchObject({
      kind: "sftp",
      purpose: "library",
      configuration: { provider: "hetzner_storage_box", port: 23 },
      secrets: { password: "test-password" },
    });
  });
});

describe("RemoteStorageConnections ownership recovery", () => {
  it("recovers failed connection reads without claiming an empty list", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json({ detail: "unavailable" }, 503),
      },
    });
    const region = screen.getByRole("region", { name: "Remote storage" });
    expect(await within(region).findByRole("alert")).toHaveTextContent(
      "Connections could not be loaded.",
    );
    expect(within(region).queryByText("No remote storage connected yet.")).not.toBeInTheDocument();
    expect(within(region).getByRole("button", { name: "Save connection" })).toBeDisabled();
    app.route({ "GET /api/v1/storage-connections": json([aStorageConnection()]) });
    await userEvent.click(within(region).getByRole("button", { name: "Retry" }));
    expect(await within(region).findByText("Workshop storage")).toBeVisible();
  });
  it("preserves a newer connection draft after an earlier save", async () => {
    const response = Promise.withResolvers<Response>();
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": () => response.promise,
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Connection name");
    await userEvent.clear(name);
    await userEvent.type(name, "Captured name");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
    await userEvent.clear(name);
    await userEvent.type(name, "Newer draft");
    await userEvent.click(screen.getByRole("button", { name: "Backup replicas" }));
    await act(async () =>
      response.resolve(json(aStorageConnection({ name: "Saved old intent", edit_version: 2 }))),
    );
    expect(await screen.findByText("Saved old intent")).toBeVisible();
    expect(name).toHaveValue("Newer draft");
    expect(screen.getByRole("button", { name: "Backup replicas" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toMatchObject({
      name: "Captured name",
    });
    let revisedHeaders: Headers | undefined;
    app.route({
      "PATCH /api/v1/storage-connections/1": (_url, init) => {
        revisedHeaders = new Headers(init?.headers);
        return json(
          aStorageConnection({ name: "Newer draft", purpose: "backup", edit_version: 3 }),
        );
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(2));
    expect(revisedHeaders?.get("If-Match")).toBe(
      `"storage-connection-1-e${aStorageConnection().edit_epoch}-v2"`,
    );
    expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toMatchObject({
      name: "Newer draft",
      purpose: "backup",
    });
  });
  it("discards typed connection credentials on private retirement", async () => {
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeRetiredStorageSecret");
    expect(screen.getByDisplayValue("FakeRetiredStorageSecret")).toBeVisible();
    await act(async () => clearLogin());
    expect(screen.queryByDisplayValue("FakeRetiredStorageSecret")).not.toBeInTheDocument();
  });
  it("cancels connection reads when their view is disposed", async () => {
    let signal: AbortSignal | null | undefined;
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": (_url, init) => {
          signal = init?.signal;
          return new Promise(() => {});
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
  });
});

describe("RemoteStorageConnections authoritative lifetimes", () => {
  it("hides warm private connections on a non-admin mount", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      auth: memberSession(),
      seed: [
        [["storage-connections"], [aStorageConnection()]],
        [["storage-providers"], storageProviderCatalogue],
      ],
    });
    expect(screen.queryByText("Workshop storage")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Connections could not be loaded.");
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
    const retry = screen.getByRole("button", { name: "Retry" });
    expect(retry).toBeDisabled();
    await userEvent.click(retry);
    expect(app.requestsWithMethod("GET")).toHaveLength(0);
  });
  it("hides cached connection content immediately after role loss", async () => {
    const app = renderApp(
      <AuthContext.Provider value={adminSession()}>
        <RemoteStorageConnections />
      </AuthContext.Provider>,
      {
        routes: {
          "GET /api/v1/storage-connections": json([aStorageConnection()]),
          "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        },
      },
    );
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeRoleLostStorageSecret");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await screen.findByRole("dialog");
    app.rerender(
      <AuthContext.Provider value={memberSession()}>
        <RemoteStorageConnections />
      </AuthContext.Provider>,
    );
    expect(screen.queryByText("Workshop storage")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("FakeRoleLostStorageSecret")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Connections could not be loaded.");
  });
  it("retries a failed provider catalogue while preserving healthy connections", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json({ detail: "unavailable" }, 503),
      },
    });
    expect(await screen.findByText("Workshop storage")).toBeVisible();
    const region = screen.getByRole("region", { name: "Remote storage" });
    expect(within(region).getByRole("alert")).toHaveTextContent(
      "Storage providers could not be loaded.",
    );
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
    app.route({ "GET /api/v1/storage/providers": json(storageProviderCatalogue) });
    await userEvent.click(within(region).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled());
    expect(
      app
        .requestsWithMethod("GET")
        .filter((request) => request.url.endsWith("/storage-connections")),
    ).toHaveLength(1);
  });
  it("preserves a transient-failure draft read-only until recovery", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Connection name");
    await userEvent.clear(name);
    await userEvent.type(name, "Keep this draft");
    app.route({ "GET /api/v1/storage-connections": json({ detail: "unavailable" }, 503) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["storage-connections"] });
    });
    await screen.findByRole("alert");
    expect(name).toHaveValue("Keep this draft");
    expect(name).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    expect(screen.getByText("Workshop storage")).toBeVisible();
    app.route({
      "GET /api/v1/storage-connections": json([aStorageConnection({ name: "Changed remotely" })]),
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(name).toBeEnabled());
    expect(name).toHaveValue("Keep this draft");
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });
  it("hides denied cached connections", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeDeniedStorageSecret");
    await userEvent.click(screen.getByRole("button", { name: "Remove" }));
    await screen.findByRole("dialog");
    app.route({ "GET /api/v1/storage-connections": json({ detail: "forbidden" }, 403) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["storage-connections"] });
    });
    await screen.findByRole("alert");
    expect(screen.queryByText("Workshop storage")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("FakeDeniedStorageSecret")).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("does not let a disposed save clear a new editor draft", async () => {
    const response = Promise.withResolvers<Response>();
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "PATCH /api/v1/storage-connections/1": () => response.promise,
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeOldStorageSecret");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
    app.unmount();
    renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeNewStorageSecret");
    await act(async () => response.resolve(json(aStorageConnection({ name: "Old ACK" }))));
    expect(screen.getByDisplayValue("FakeNewStorageSecret")).toBeVisible();
    expect(screen.queryByText("Remote storage connection saved.")).not.toBeInTheDocument();
    expect(screen.queryByText("Old ACK")).not.toBeInTheDocument();
  });
  it("blocks an edit whose connection disappeared from a current read", async () => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Connection name");
    await userEvent.clear(name);
    await userEvent.type(name, "Kept removed draft");
    app.route({ "GET /api/v1/storage-connections": json([]) });
    await act(async () => {
      await app.client.invalidateQueries({ queryKey: ["storage-connections"] });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "This connection is no longer available.",
    );
    expect(name).toHaveValue("Kept removed draft");
    expect(name).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
  });
});

describe("Remote connection conditional editing", () => {
  it.each([412, 503])(
    "%s: reviews a connection before revising deliberate fields",
    async (status) => {
      const original = aStorageConnection();
      const latest = aStorageConnection({
        name: "Other administrator",
        purpose: "backup",
        edit_version: 2,
      });
      const writes: Headers[] = [];
      let reads = 0;
      const app = renderApp(<RemoteStorageConnections />, {
        routes: {
          "GET /api/v1/storage/providers": json(storageProviderCatalogue),
          "GET /api/v1/storage-connections": () => json([++reads === 1 ? original : latest]),
          "PATCH /api/v1/storage-connections/1": (_url, init) => {
            writes.push(new Headers(init?.headers));
            return writes.length === 1
              ? json({ detail: "edit_conflict" }, status)
              : json({ ...latest, name: "My draft", edit_version: 3 });
          },
        },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
      await userEvent.clear(screen.getByLabelText("Connection name"));
      await userEvent.type(screen.getByLabelText("Connection name"), "My draft");
      await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
      await screen.findByRole("button", { name: "Review current values" });
      expect(screen.getByLabelText("Connection name")).toHaveValue("My draft");
      expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
      await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
      const preview = await screen.findByRole("region", { name: "Latest saved version" });
      expect(within(preview).getByText("Other administrator")).toBeVisible();
      await userEvent.click(screen.getByRole("button", { name: "Save revised changes" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save connection" })).toBeVisible(),
      );
      expect(writes.map((headers) => headers.get("If-Match"))).toEqual([
        `"storage-connection-1-e${original.edit_epoch}-v1"`,
        `"storage-connection-1-e${original.edit_epoch}-v2"`,
      ]);
      expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toEqual({
        name: "My draft",
        configuration: {},
        secrets: {},
      });
      expect(screen.getByRole("combobox", { name: "Use My draft for" })).toHaveValue("backup");
    },
  );
  it("adopts a fresh connection after a replaced preview", async () => {
    let reads = 0;
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": () =>
          json([
            aStorageConnection({
              name:
                ++reads === 1
                  ? "Workshop storage"
                  : reads === 2
                    ? "Replacement preview"
                    : "Current replacement",
              edit_epoch: reads === 1 ? "a".repeat(32) : "b".repeat(32),
            }),
          ]),
        "PATCH /api/v1/storage-connections/1": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeDraftOnlySecret");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    expect(await screen.findByText("Replacement preview")).toBeVisible();
    expect(screen.getByRole("button", { name: "Save revised changes" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Use current values" }));
    await waitFor(() =>
      expect(screen.getByLabelText("Connection name")).toHaveValue("Current replacement"),
    );
    expect(screen.getByLabelText("Secret key")).toHaveValue("");
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it.each([403, 404])("%s: retires an unavailable connection review", async (status) => {
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeRetiredReviewSecret");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByRole("button", { name: "Review current values" });
    app.route({ "GET /api/v1/storage-connections": json({ detail: "unavailable" }, status) });
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Review current values" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.queryByRole("region", { name: "Latest saved version" })).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
});

describe("Connection review lifetime", () => {
  it("keeps a failed pause intent through review", async () => {
    let reads = 0;
    let writes = 0;
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": () =>
          json([
            aStorageConnection({
              edit_version: ++reads,
              name: reads === 1 ? "Workshop storage" : "Reviewed connection",
            }),
          ]),
        "PATCH /api/v1/storage-connections/1": () =>
          ++writes === 1
            ? json({ detail: "edit_conflict" }, 412)
            : json(
                aStorageConnection({
                  edit_version: 3,
                  name: "Reviewed connection",
                  enabled: false,
                }),
              ),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Pause" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review current values" }));
    await userEvent.click(await screen.findByRole("button", { name: "Save revised changes" }));
    expect(await screen.findByRole("button", { name: "Resume" })).toBeVisible();
    expect(app.requestsWithMethod("PATCH").map((request) => JSON.parse(request.body))).toEqual([
      { enabled: false },
      { enabled: false },
    ]);
  });
  it("discards a held review on logout", async () => {
    const held = Promise.withResolvers<Response>();
    const app = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit" }));
    await userEvent.type(screen.getByLabelText("Secret key"), "FakeReviewRetiredSecret");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    await screen.findByRole("button", { name: "Review current values" });
    app.route({ "GET /api/v1/storage-connections": () => held.promise });
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    act(() => clearLogin());
    await act(async () =>
      held.resolve(
        json([aStorageConnection({ name: "Retired private preview", edit_version: 2 })]),
      ),
    );
    expect(screen.queryByText("Retired private preview")).not.toBeInTheDocument();
    expect(screen.queryByDisplayValue("FakeReviewRetiredSecret")).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
});
