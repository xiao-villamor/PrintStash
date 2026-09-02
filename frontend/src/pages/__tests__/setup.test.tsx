/*
 * First run: the one screen a self-hoster cannot get past by trying again.
 *
 * Everything here is about not leaving the operator stuck. Validation is inline
 * and before advancing, because a rejection after the account step means retyping
 * a password they cannot see. A recoverable failure preserves what they entered —
 * losing a filled form on a transient error is how people give up on
 * self-hosting. And duplicate submissions are blocked while the request is in
 * flight, since setup is not idempotent: two completions race to create the same
 * admin.
 *
 * The private-vault message is the one piece of real advice on the page: pointing
 * PrintStash's own storage at an existing library folder would have it index and
 * then manage somebody else's files. Explaining that is cheaper than the support
 * thread.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { usePathname } from "@/lib/navigation";
import SetupPage, { type SetupPageDeps } from "@/pages/setup";
import type { SetupResponse, SetupStatus, StorageProvider } from "@/types";

// The wizard takes its endpoints and login store as an injected `deps` bag, and
// navigates through the real router, so this test needs no module replacement.
function stubDeps(): SetupPageDeps {
  return {
    getSetupStatus: vi.fn<SetupPageDeps["getSetupStatus"]>(),
    getStorageProviders: vi.fn<SetupPageDeps["getStorageProviders"]>(),
    completeSetup: vi.fn<SetupPageDeps["completeSetup"]>(),
    storeLogin: vi.fn<SetupPageDeps["storeLogin"]>(),
  };
}

let deps = stubDeps();

const status: SetupStatus = {
  configured: false,
  setup_token_required: true,
  user_count: 0,
  default_data_dir: "/data/files",
  default_thumb_dir: "/data/thumbs",
  current_data_dir: "/data/files",
  current_thumb_dir: "/data/thumbs",
  current_storage_backend: "local",
  current_s3_bucket: "",
  current_s3_endpoint_url: "",
  current_s3_region: "auto",
  current_backup_retention_days: 30,
  current_backup_s3_bucket: "",
  current_backup_s3_endpoint_url: "",
  current_backup_s3_region: "auto",
  configured_at: null,
};

const providers: StorageProvider[] = [
  {
    id: "local",
    label: "Local disk",
    category: "this_machine",
    description: "Private directories on this machine.",
    expected_tier: "verified",
    expected_tier_note: "Local storage is verified after startup probes.",
    consequences: [],
    documentation_url: "/docs/storage-providers.md#local",
    available: true,
    selectable: true,
    fields: [
      {
        name: "data_dir",
        label: "Data directory",
        help: "Private model storage.",
        input_type: "path",
        required: true,
        secret: false,
      },
      {
        name: "thumb_dir",
        label: "Thumbnail directory",
        help: "Private thumbnail storage.",
        input_type: "path",
        required: true,
        secret: false,
      },
    ],
  },
  {
    id: "s3",
    label: "Amazon S3",
    category: "s3_compatible",
    description: "Store artifacts in an S3 bucket.",
    expected_tier: "guarded",
    expected_tier_note: "Object versions guard destructive operations.",
    consequences: [],
    documentation_url: "/docs/storage-providers.md#s3",
    available: true,
    selectable: true,
    fields: [
      {
        name: "bucket",
        label: "Bucket",
        help: "Operator-provisioned bucket.",
        input_type: "text",
        required: true,
        secret: false,
      },
    ],
  },
  {
    id: "sftp",
    label: "SFTP",
    category: "nas_sftp",
    description: "NAS storage over SSH File Transfer Protocol.",
    expected_tier: "guarded",
    expected_tier_note: "SFTP cannot prove conditional ownership.",
    consequences: [],
    documentation_url: "/docs/storage-providers.md#sftp",
    available: true,
    selectable: true,
    fields: [
      {
        name: "host",
        label: "Host",
        help: "SFTP hostname.",
        input_type: "text",
        required: true,
        secret: false,
      },
      {
        name: "port",
        label: "Port",
        help: "SFTP port.",
        input_type: "number",
        required: true,
        secret: false,
        default: 22,
      },
      {
        name: "username",
        label: "Username",
        help: "SFTP account username.",
        input_type: "text",
        required: true,
        secret: false,
      },
      {
        name: "host_key",
        label: "Host key",
        help: "OpenSSH known-host entry.",
        input_type: "text",
        required: true,
        secret: false,
      },
      {
        name: "password",
        label: "Password",
        help: "Optional password.",
        input_type: "password",
        required: false,
        secret: true,
      },
      {
        name: "private_key_path",
        label: "Private key path",
        help: "Mounted private key path.",
        input_type: "path",
        required: false,
        secret: false,
      },
    ],
  },
];

const setupResponse: SetupResponse = {
  configured: true,
  user_id: 1,
  username: "admin",
  storage_backend: "local",
  storage_provider: "local",
  data_dir: "/data/files",
  thumb_dir: "/data/thumbs",
  access_token: "token",
  token_type: "bearer",
};

/** Surfaces the router's current path so navigation can be asserted on. */
function CurrentPath() {
  return <span data-testid="current-path">{usePathname()}</span>;
}

function currentPath(): string {
  return screen.getByTestId("current-path").textContent ?? "";
}

function renderSetup() {
  render(
    <MemoryRouter initialEntries={["/setup"]}>
      <SetupPage deps={deps} />
      <CurrentPath />
    </MemoryRouter>,
  );
}

async function reachStorage() {
  const user = userEvent.setup();
  renderSetup();
  await screen.findByRole("heading", { name: "Welcome to PrintStash" });
  await user.type(screen.getByLabelText("Setup token"), "operator-setup-token-123");
  await user.type(screen.getByLabelText("Username"), "admin");
  await user.type(screen.getByLabelText("Password"), "Password123");
  await user.type(screen.getByLabelText("Confirm password"), "Password123");
  await user.click(screen.getByRole("button", { name: "Next" }));
  return user;
}

beforeEach(() => {
  deps = stubDeps();
  vi.mocked(deps.getSetupStatus).mockResolvedValue(status);
  vi.mocked(deps.getStorageProviders).mockResolvedValue(providers);
  vi.mocked(deps.completeSetup).mockResolvedValue(setupResponse);
});

describe("SetupPage", () => {
  it("validates account fields inline before advancing", async () => {
    const user = userEvent.setup();
    renderSetup();
    await screen.findByRole("heading", { name: "Welcome to PrintStash" });
    await user.type(screen.getByLabelText("Setup token"), "operator-setup-token-123");
    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Username must be at least 3 characters");
    expect(deps.completeSetup).not.toHaveBeenCalled();
  });

  it("authenticates and enters empty library after successful setup", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    await waitFor(() =>
      expect(deps.storeLogin).toHaveBeenCalledWith(
        "token",
        expect.objectContaining({ username: "admin" }),
      ),
    );
    expect(deps.completeSetup).toHaveBeenCalledWith(
      expect.objectContaining({ setup_token: "operator-setup-token-123" }),
    );
    await waitFor(() => expect(currentPath()).toBe("/"));
  });

  it("preserves values after recoverable failure and allows safe retry", async () => {
    vi.mocked(deps.completeSetup)
      .mockRejectedValueOnce(new Error('HTTP 400: {"detail":"data_dir_not_writable"}'))
      .mockResolvedValueOnce(setupResponse);
    const user = await reachStorage();
    await user.clear(screen.getByLabelText("Data directory"));
    await user.type(screen.getByLabelText("Data directory"), "/recoverable/path");
    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "cannot write to the data directory",
    );
    expect(screen.getByLabelText("Data directory")).toHaveValue("/recoverable/path");
    await user.click(screen.getByRole("button", { name: "Complete setup" }));
    await waitFor(() => expect(deps.completeSetup).toHaveBeenCalledTimes(2));
  });

  it("explains that an existing library cannot be used as private vault storage", async () => {
    vi.mocked(deps.completeSetup).mockRejectedValueOnce(
      new Error('HTTP 400: {"detail":"data_dir_not_empty"}'),
    );
    const user = await reachStorage();

    await user.click(screen.getByRole("button", { name: "Complete setup" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("dedicated empty directory");
    expect(screen.getByRole("alert")).toHaveTextContent("Library sources");
  });

  it("blocks duplicate completion submissions while request is active", async () => {
    let resolve!: (value: SetupResponse) => void;
    vi.mocked(deps.completeSetup).mockReturnValue(
      new Promise<SetupResponse>((done) => {
        resolve = done;
      }),
    );
    const user = await reachStorage();
    const submit = screen.getByRole("button", { name: "Complete setup" });
    await user.dblClick(submit);
    expect(deps.completeSetup).toHaveBeenCalledTimes(1);
    resolve(setupResponse);
  });

  it("keeps optional off-site backup settings collapsed until requested", async () => {
    const user = await reachStorage();

    expect(screen.getByLabelText("Backup bucket")).not.toBeVisible();
    await user.click(screen.getByText(/Legacy S3 off-site backup/));
    expect(screen.getByLabelText("Backup bucket")).toBeVisible();
  });
  it("refuses S3 storage with no bucket named", async () => {
    // The wizard is the only chance to get this right: a vault that boots
    // pointed at no bucket cannot write its first artifact, and the operator
    // has no UI yet to fix it from.
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "S3-compatible object storage" }));
    await user.click(screen.getByRole("button", { name: /Amazon S3/ }));

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("Bucket is required.");
  });

  it("refuses local storage with a directory left blank", async () => {
    const user = await reachStorage();
    await user.clear(screen.getByLabelText("Data directory"));

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("Data directory is required.");
  });

  it("refuses a negative backup retention", async () => {
    // Negative days would be read as a window, and the first scheduled purge
    // would take everything.
    const user = await reachStorage();
    fireEvent.change(screen.getByLabelText("Retention days"), { target: { value: "-5" } });

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("Backup retention must be 0 or more days.");
  });

  it("accepts a retention of zero, which means keep nothing", async () => {
    const user = await reachStorage();
    const days = screen.getByLabelText("Retention days");
    await user.clear(days);
    await user.type(days, "0");

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("requires the SFTP host key before completion", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "NAS over SFTP" }));
    await user.click(screen.getByRole("button", { name: /^SFTP/ }));
    await user.type(screen.getByLabelText("Host"), "nas.example.test");
    await user.type(screen.getByLabelText("Username"), "printstash");

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    expect(screen.getByRole("alert")).toHaveTextContent("Host key is required.");
    expect(deps.completeSetup).not.toHaveBeenCalled();
  });

  it("submits the SFTP host key through provider configuration", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "NAS over SFTP" }));
    await user.click(screen.getByRole("button", { name: /^SFTP/ }));
    await user.type(screen.getByLabelText("Host"), "nas.example.test");
    await user.type(screen.getByLabelText("Username"), "printstash");
    await user.type(screen.getByLabelText("Host key"), "nas.example.test ssh-ed25519 AAAA");

    await user.click(screen.getByRole("button", { name: /Complete setup/ }));

    await waitFor(() =>
      expect(deps.completeSetup).toHaveBeenCalledWith(
        expect.objectContaining({
          storage_provider: "sftp",
          storage_provider_config: expect.objectContaining({
            provider: "sftp",
            host_key: "nas.example.test ssh-ed25519 AAAA",
          }),
        }),
      ),
    );
  });
});
