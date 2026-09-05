/**
 * Remote storage profiles are configured once and consumed by two independent
 * workflows. These tests defend the usage assignment and write-only credential
 * boundary so moving the form out of Backup and Library sources cannot silently
 * narrow a shared profile or send a secret back in a later update.
 */
import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { RemoteStorageConnections } from "@/components/remote-storage-connections";
import { storageProviderCatalogue } from "@/test-support/storage-provider-catalogue";
import { aStorageConnection } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

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
    expect(screen.getByRole("option", { name: "Amazon S3 or compatible" })).toBeDisabled();
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

  it("creates a Google Drive profile for both workflows", async () => {
    const user = userEvent.setup();
    const created = aStorageConnection({
      id: 9,
      name: "Family Drive",
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

    await user.type(screen.getByLabelText("Connection name"), "Family Drive");
    await user.selectOptions(screen.getByLabelText("Provider"), "gdrive");
    await user.type(screen.getByLabelText("OAuth client ID"), "google-client");
    await user.type(screen.getByLabelText("OAuth client secret"), "google-secret");
    await user.type(screen.getByLabelText("Refresh token"), "google-refresh");
    await user.click(screen.getByRole("button", { name: "Save connection" }));

    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(1));
    expect(JSON.parse(view.requestsWithMethod("POST")[0].body)).toEqual({
      name: "Family Drive",
      kind: "gdrive",
      purpose: "both",
      configuration: { provider: "gdrive", client_id: "google-client", root: "PrintStash" },
      secrets: { client_secret: "google-secret", refresh_token: "google-refresh" },
    });
    expect(await screen.findByText("Family Drive")).toBeVisible();
    expect(screen.queryByDisplayValue("google-secret")).toBeNull();
  });

  it("changes which workflows may reuse a connection", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ purpose: "library" })),
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

  it("pauses a connection without changing its uses", async () => {
    const user = userEvent.setup();
    const view = renderApp(<RemoteStorageConnections />, {
      routes: {
        "GET /api/v1/storage/providers": json(storageProviderCatalogue),
        "GET /api/v1/storage-connections": json([aStorageConnection()]),
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ enabled: false })),
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
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection({ name: "Renamed" })),
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
        "PATCH /api/v1/storage-connections/1": json(aStorageConnection()),
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
