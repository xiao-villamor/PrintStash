import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { BackupDestinations } from "@/components/backup-destinations";
import { json, renderApp } from "@/test-support/render";
import type { StorageConnection } from "@/types";

function connection(overrides: Partial<StorageConnection> = {}): StorageConnection {
  return {
    id: 7,
    name: "Off-site Drive",
    kind: "gdrive",
    purpose: "backup",
    configuration: { client_id: "client", root: "PrintStash/backups" },
    secret_fields_set: ["client_secret", "refresh_token"],
    enabled: true,
    ...overrides,
  };
}

describe("BackupDestinations", () => {
  it("shows only purpose-scoped backup connections", async () => {
    const user = userEvent.setup();
    const view = renderApp(<BackupDestinations />, {
      routes: {
        "GET /api/v1/storage-connections": json([
          connection(),
          connection({ id: 8, name: "Models bucket", kind: "s3", purpose: "library" }),
        ]),
        "PATCH /api/v1/storage-connections/7": json(connection({ enabled: false })),
      },
    });

    expect(await screen.findByText("Off-site Drive")).toBeInTheDocument();
    expect(screen.queryByText("Models bucket")).not.toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Pause" }));

    expect(await screen.findByText("Paused")).toBeInTheDocument();
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body)).toEqual({ enabled: false });
  });

  it("creates and probes a Google Drive backup profile without returning secrets", async () => {
    const user = userEvent.setup();
    const created = connection({ id: 9, name: "Family Drive" });
    const view = renderApp(<BackupDestinations />, {
      routes: {
        "GET /api/v1/storage-connections": json([]),
        "POST /api/v1/storage-connections/9/probe": json({ ok: true }),
        "POST /api/v1/storage-connections": json(created, 201),
      },
    });

    await screen.findByText(
      "No remote replicas configured. Local backups continue to work normally.",
    );
    await user.type(screen.getByLabelText("Destination name"), "Family Drive");
    await user.selectOptions(screen.getByLabelText("Provider"), "gdrive");
    await user.type(screen.getByLabelText("OAuth client ID"), "google-client");
    await user.type(screen.getByLabelText("OAuth client secret"), "google-secret");
    await user.type(screen.getByLabelText("Offline refresh token"), "google-refresh");
    await user.click(screen.getByRole("button", { name: "Save and test destination" }));

    await waitFor(() => expect(view.requestsWithMethod("POST")).toHaveLength(2));
    const body = JSON.parse(view.requestsWithMethod("POST")[0].body);
    expect(body).toEqual({
      name: "Family Drive",
      kind: "gdrive",
      purpose: "backup",
      configuration: {
        client_id: "google-client",
        root: "PrintStash/backups",
      },
      secrets: {
        client_secret: "google-secret",
        refresh_token: "google-refresh",
      },
    });
    expect(screen.getByText(/not deleted by automatic retention/)).toBeInTheDocument();
    expect(await screen.findByText("Backup destination connected.")).toBeInTheDocument();
  });
});
