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

import { I18nProvider } from "@/lib/i18n";
import { usePathname } from "@/lib/navigation";
import SetupPage, { type SetupPageDeps } from "@/pages/setup";
import type { SetupResponse, SetupStatus, StorageProvider } from "@/types";

// The wizard takes its endpoints and login store as an injected `deps` bag, and
// navigates through the real router, so this test needs no module replacement.
function stubDeps(): SetupPageDeps {
  return {
    beginSetup: vi
      .fn<SetupPageDeps["beginSetup"]>()
      .mockResolvedValue({ csrf: "automatic-csrf", expires_in: 3600 }),
    checkSetupStorage: vi
      .fn<SetupPageDeps["checkSetupStorage"]>()
      .mockResolvedValue({ ready: true, storage_provider: "local", checks: [] }),
    getSetupStatus: vi.fn<SetupPageDeps["getSetupStatus"]>(),
    getStorageProviders: vi.fn<SetupPageDeps["getStorageProviders"]>(),
    completeSetup: vi.fn<SetupPageDeps["completeSetup"]>(),
    storeLogin: vi.fn<SetupPageDeps["storeLogin"]>(),
  };
}

let deps = stubDeps();

const status: SetupStatus = {
  configured: false,
  setup_available: true,
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
  storage_ready: true,
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
      <I18nProvider>
        <SetupPage deps={deps} />
      </I18nProvider>
      <CurrentPath />
    </MemoryRouter>,
  );
}

async function reachStorage() {
  const user = userEvent.setup();
  renderSetup();
  await screen.findByLabelText("Username");
  await user.type(screen.getByLabelText("Username"), "admin");
  await user.type(screen.getByLabelText("Password"), "Password123");
  await user.type(screen.getByLabelText("Confirm password"), "Password123");
  await user.click(screen.getByRole("button", { name: "Continue" }));
  return user;
}

beforeEach(() => {
  localStorage.setItem("printstash.locale", "en");
  deps = stubDeps();
  vi.mocked(deps.getSetupStatus).mockResolvedValue(status);
  vi.mocked(deps.getStorageProviders).mockResolvedValue(providers);
  vi.mocked(deps.completeSetup).mockResolvedValue(setupResponse);
});

describe("SetupPage", () => {
  it("focuses the first invalid account field", async () => {
    renderSetup();
    await screen.findByLabelText("Username");
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    expect(screen.getByLabelText("Username")).toHaveFocus();
    expect(screen.getByLabelText("Username")).toHaveAttribute("aria-invalid", "true");
  });
  it("does not ask for a manual setup credential", async () => {
    renderSetup();
    await screen.findByLabelText("Username");
    expect(screen.queryByLabelText(/setup token/i)).not.toBeInTheDocument();
  });
  it("preserves the account when going back", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByLabelText("Password")).toHaveValue("Password123");
  });
  it("requires a successful check before creating an account", async () => {
    await reachStorage();
    expect(screen.getByRole("button", { name: "Create my account and continue" })).toBeDisabled();
  });
  it("continues the guide with the authenticated administrator", async () => {
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    await screen.findByText("Storage ready");
    await user.click(screen.getByRole("button", { name: "Create my account and continue" }));
    await waitFor(() => expect(currentPath()).toBe("/getting-started"));
    expect(deps.storeLogin).toHaveBeenCalledWith(
      "token",
      expect.objectContaining({ username: "admin" }),
    );
  });
  it("retains the form after a recoverable creation error", async () => {
    vi.mocked(deps.completeSetup).mockRejectedValueOnce(new Error("data_dir_not_writable"));
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    await screen.findByText("Storage ready");
    await user.click(screen.getByRole("button", { name: "Create my account and continue" }));
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(screen.getByLabelText("Password")).toHaveValue("Password123");
  });
  it("offers login when the creation response was lost after commit", async () => {
    const user = await reachStorage();
    vi.mocked(deps.completeSetup).mockRejectedValueOnce(new Error("network lost"));
    vi.mocked(deps.getSetupStatus).mockResolvedValue({ configured: true, user_count: 0 });
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    await screen.findByText("Storage ready");
    await user.click(screen.getByRole("button", { name: "Create my account and continue" }));
    expect(await screen.findByRole("button", { name: "Sign in" })).toBeVisible();
    expect(deps.completeSetup).toHaveBeenCalledTimes(1);
  });
  it("explains how to connect a populated folder", async () => {
    vi.mocked(deps.checkSetupStorage).mockRejectedValueOnce(new Error("data_dir_not_empty"));
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Library source");
  });
  it("does not call unchecked remote storage ready", async () => {
    vi.mocked(deps.checkSetupStorage).mockResolvedValueOnce({
      ready: false,
      storage_provider: "s3",
      checks: [{ code: "remote_connection_not_checked", free_bytes: null }],
    });
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    expect(await screen.findByText("Needs attention")).toBeVisible();
    expect(screen.getByRole("button", { name: "Create my account and continue" })).toBeDisabled();
  });
  it("shows disabled registration without account inputs", async () => {
    vi.mocked(deps.getSetupStatus).mockResolvedValue({
      configured: false,
      setup_available: false,
      user_count: 0,
    });
    renderSetup();
    expect(await screen.findByRole("alert")).toHaveTextContent("Initial registration is disabled");
    expect(screen.queryByLabelText("Username")).not.toBeInTheDocument();
  });
  it("lets a password manager generated password be inspected", async () => {
    renderSetup();
    const password = await screen.findByLabelText("Password");
    fireEvent.click(screen.getByRole("button", { name: "Show passwords" }));
    expect(password).toHaveAttribute("type", "text");
  });
  it("offers Spanish from the first screen", async () => {
    renderSetup();
    await screen.findByLabelText("Username");
    fireEvent.click(screen.getByRole("button", { name: /Language: English/ }));
    expect(await screen.findByLabelText("Usuario")).toBeVisible();
  });
});

describe("Storage form recovery", () => {
  it("preserves storage locations when the language changes", async () => {
    const user = await reachStorage();
    await user.click(screen.getByText("View location and advanced options"));
    await user.clear(screen.getByLabelText("Data directory"));
    await user.type(screen.getByLabelText("Data directory"), "/custom/files");
    await user.click(screen.getByRole("button", { name: /Language: English/ }));
    expect(screen.getByDisplayValue("/custom/files")).toBeInTheDocument();
  });

  it("invalidates a check when its storage values have changed", async () => {
    let completeCheck!: (value: Awaited<ReturnType<SetupPageDeps["checkSetupStorage"]>>) => void;
    vi.mocked(deps.checkSetupStorage).mockReturnValueOnce(
      new Promise((resolve) => {
        completeCheck = resolve;
      }),
    );
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    await user.click(screen.getByText("View location and advanced options"));
    await user.clear(screen.getByLabelText("Data directory"));
    await user.type(screen.getByLabelText("Data directory"), "/different/files");
    completeCheck({ ready: true, storage_provider: "local", checks: [] });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Check storage" })).toBeEnabled(),
    );
    expect(screen.getByRole("button", { name: "Create my account and continue" })).toBeDisabled();
  });

  it("identifies measured capacity as available space", async () => {
    vi.mocked(deps.checkSetupStorage).mockResolvedValueOnce({
      ready: true,
      storage_provider: "local",
      checks: [{ code: "data_writable", free_bytes: 1024 }],
    });
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    expect(await screen.findByText(/Your files:.*available/)).toBeVisible();
  });

  it("announces account creation while the request is pending", async () => {
    vi.mocked(deps.completeSetup).mockReturnValueOnce(new Promise(() => {}));
    const user = await reachStorage();
    await user.click(screen.getByRole("button", { name: "Check storage" }));
    await screen.findByText("Storage ready");
    await user.click(screen.getByRole("button", { name: "Create my account and continue" }));
    expect(await screen.findByText("Creating your account…")).toBeVisible();
    expect(screen.getByRole("button", { name: "Edit" })).toBeDisabled();
  });
});
