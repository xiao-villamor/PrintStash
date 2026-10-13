/*
 * Where the vault keeps its bytes.
 *
 * This is the highest-consequence form in the product: the backend and the
 * paths under it decide where every artifact is written and read. Point it at
 * the wrong place and the library is intact but unreachable, which looks
 * identical to data loss until somebody finds the old directory.
 *
 * The S3 credentials are the reason for the care taken here. They are stored,
 * never returned, and rendered as a masked placeholder — so a field the user did
 * not touch must not travel at all. Sending the mask back replaces a working key
 * with asterisks, and the vault stops being able to read its own files.
 *
 * Local and S3 are mutually exclusive, and the fields belonging to the other one
 * are hidden rather than disabled: an S3 bucket typed into a local deployment is
 * a value that looks configured and does nothing.
 *
 * A backend change needs a restart to take effect, and the card says so —
 * otherwise the operator saves, sees "Saved", and concludes the setting is live
 * when nothing has moved.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { queryKeys } from "@/lib/query-client";
import { clearLogin } from "@/lib/auth-store";
import { vaultConfigOptions } from "@/lib/queries/settings-config";
import { StorageConfigCard } from "@/components/storage-config-card";
import { VaultMigrationPanel } from "@/components/vault-migration-panel";
import { aMigrationBackup, aVaultMigration, aVaultConfig } from "@/test-support/factories";
import { adminSession, json, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { StorageHealthRead, StorageProvider, VaultConfigRead } from "@/types";

const PROVIDERS: StorageProvider[] = [
  {
    id: "local",
    label: "Local disk",
    category: "this_machine",
    description: "Store artifacts on this machine.",
    expected_tier: "verified",
    expected_tier_note: "Local inode identity supports verified deletion.",
    consequences: [],
    documentation_url: "/docs/storage-providers.md#local",
    available: true,
    selectable: true,
    fields: [
      {
        name: "data_dir",
        label: "Data directory",
        help: "Private artifact directory.",
        input_type: "path",
        required: true,
        secret: false,
      },
      {
        name: "thumb_dir",
        label: "Thumbnail directory",
        help: "Regenerable preview directory.",
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
      {
        name: "access_key",
        label: "Access key",
        help: "Write-only credential.",
        input_type: "password",
        required: false,
        secret: true,
      },
    ],
  },
  {
    id: "sftp",
    label: "SFTP",
    category: "nas_sftp",
    description: "Store artifacts on a NAS over SFTP.",
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

function anS3Config(over: Partial<VaultConfigRead> = {}): VaultConfigRead {
  return aVaultConfig({
    storage_backend: "s3",
    storage_provider: "s3",
    storage_provider_config: {
      provider: "s3",
      bucket: "vault-prod",
    },
    storage_tier: "guarded",
    ...over,
  });
}

function renderCard(
  options: RenderAppOptions & {
    config?: VaultConfigRead;
    storageHealth?: StorageHealthRead;
    migrationManaged?: boolean;
  } = {},
) {
  const {
    config = aVaultConfig(),
    storageHealth,
    migrationManaged,
    routes = {},
    ...rest
  } = options;
  return renderApp(
    <StorageConfigCard storageHealth={storageHealth} migrationManaged={migrationManaged} />,
    {
      routes: {
        "GET /api/v1/config": json(config),
        "GET /api/v1/storage/providers": json(PROVIDERS),
        "GET /api/v1/storage-connections": json([]),
        "PUT /api/v1/config": json({ ...config, edit_version: config.edit_version + 1 }),
        ...routes,
      },
      ...rest,
    },
  );
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("StorageConfigCard", () => {
  it("reserves the current storage layout while configuration loads", async () => {
    let finish: (response: Response) => void = () => {};
    const pending = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    renderCard({ routes: { "GET /api/v1/config": () => pending } });

    expect(screen.getByRole("status", { name: "Current storage" })).toBeVisible();
    finish(json(aVaultConfig()));
    expect(await screen.findByDisplayValue("/data/files")).toBeVisible();
    expect(screen.queryByRole("status", { name: "Current storage" })).toBeNull();
  });

  describe("a local deployment", () => {
    it("explains a missing root without offering unsafe acknowledgement", async () => {
      renderCard({
        storageHealth: {
          ok: false,
          provider: "local",
          tier: "guarded",
          diagnostics: { root_bindings: { data: "binding_missing" } },
        },
      });

      const alert = await screen.findByRole("alert");
      expect(alert).toHaveTextContent("Storage needs attention");
      expect(alert).toHaveTextContent("Do not acknowledge this warning");
    });

    it("warns that imports copy when staging cannot hard-link", async () => {
      renderCard({
        storageHealth: {
          ok: true,
          provider: "local",
          tier: "verified",
          diagnostics: { staged_hardlink: false },
        },
      });

      await waitFor(() =>
        expect(screen.getByRole("status")).toHaveTextContent("Imports are copied, not hard-linked"),
      );
    });

    it("offers explicit enrollment for a missing legacy marker", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        storageHealth: {
          ok: false,
          provider: "local",
          tier: "guarded",
          data_dir: "/data/files",
          diagnostics: { root_bindings: { data: "binding_missing" } },
        },
        routes: {
          "POST /api/v1/config/storage-roots/enroll": json({
            enrolled: true,
            role: "data",
            restart_required: true,
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Review and enroll" }));
      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("/data/files");
      expect(dialog).toHaveTextContent("wrong disk");
      await user.click(within(dialog).getByRole("button", { name: "Enroll root" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("storage-roots/enroll")),
        ).toBe(true),
      );
      expect(
        JSON.parse(
          requestsWithMethod("POST").find((call) => call.url.includes("storage-roots/enroll"))!
            .body,
        ),
      ).toEqual({ role: "data", confirm: true, expected_path: "/data/files" });
      expect(await screen.findByText(/Storage root enrolled/i)).toBeVisible();
    });

    it("does not offer enrollment when the root binding mismatches", async () => {
      renderCard({
        storageHealth: {
          ok: false,
          provider: "local",
          tier: "guarded",
          data_dir: "/data/files",
          diagnostics: { root_bindings: { data: "binding_mismatch" } },
        },
      });

      await screen.findAllByRole("alert");
      expect(screen.queryByRole("button", { name: "Review and enroll" })).toBeNull();
      expect(screen.getByText(/binding_mismatch/)).toBeInTheDocument();
    });

    it("shows where artifacts are written", async () => {
      renderCard();

      expect(await screen.findByDisplayValue("/data/files")).toBeInTheDocument();
    });

    it("shows where thumbnails are written", async () => {
      // They are a separate directory on purpose: they are regenerable, so an
      // operator may want them off the backed-up volume.
      renderCard();

      expect(await screen.findByDisplayValue("/data/thumbs")).toBeInTheDocument();
    });

    it("hides the S3 fields entirely", async () => {
      // A bucket typed into a local deployment is a value that looks configured
      // and does nothing.
      renderCard();

      await screen.findByDisplayValue("/data/files");
      expect(screen.queryByPlaceholderText("my-vault-bucket")).toBeNull();
    });

    it("warns that a backend change needs a restart", async () => {
      // Without it the operator saves, reads "Saved", and concludes the setting
      // is live when nothing has moved.
      renderCard();

      expect(
        await screen.findByText(/Provider changes require an application restart/),
      ).toBeInTheDocument();
    });
  });

  describe("an S3 deployment", () => {
    it("shows the bucket it writes to", async () => {
      renderCard({ config: anS3Config() });

      expect(await screen.findByDisplayValue("vault-prod")).toBeInTheDocument();
    });

    it("hides the local paths", async () => {
      renderCard({ config: anS3Config() });

      await screen.findByDisplayValue("vault-prod");
      expect(screen.queryByDisplayValue("/data/files")).toBeNull();
    });

    it("says a stored access key exists without showing it", async () => {
      // The server never returns it; a blank field would read as no key at all.
      renderCard({
        config: anS3Config({
          storage_provider_config: {
            provider: "s3",
            bucket: "vault-prod",
            secret_fields_set: ["access_key"],
          },
        }),
      });

      expect(
        await screen.findByPlaceholderText("Stored — leave blank to keep"),
      ).toBeInTheDocument();
    });

    it("swaps to S3 when the operator chooses it", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByDisplayValue("/data/files");

      await user.click(screen.getByRole("button", { name: "S3-compatible object storage" }));
      await user.click(screen.getByRole("button", { name: /Amazon S3/ }));

      expect(screen.getByLabelText("Bucket")).toBeInTheDocument();
    });
  });

  describe("saving", () => {
    it("sends the backend the operator chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByDisplayValue("/data/files");
      await user.click(screen.getByRole("button", { name: "S3-compatible object storage" }));
      await user.click(screen.getByRole("button", { name: /Amazon S3/ }));

      await user.type(screen.getByLabelText("Bucket"), "chosen-bucket");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          storage_provider: "s3",
          storage_provider_config: { provider: "s3" },
        }),
      );
    });

    it("refuses a provider whose required configuration is missing", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      await screen.findByDisplayValue("/data/files");
      await user.click(screen.getByRole("button", { name: "S3-compatible object storage" }));
      await user.click(screen.getByRole("button", { name: /Amazon S3/ }));
      await user.click(screen.getByRole("button", { name: /Save configuration/ }));
      expect(await screen.findByText("Bucket is required.")).toBeVisible();
      expect(requestsWithMethod("PUT")).toHaveLength(0);
    });

    it("sends the paths the operator typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard();
      const dataDir = await screen.findByDisplayValue("/data/files");
      await user.clear(dataDir);
      await user.type(dataDir, "/mnt/vault");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          storage_provider_config: { data_dir: "/mnt/vault" },
        }),
      );
    });

    it("leaves a stored credential alone when it was not retyped", async () => {
      // Sending the mask back replaces a working key with asterisks, and the
      // vault stops being able to read its own files.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        config: anS3Config({
          storage_provider_config: {
            provider: "s3",
            bucket: "vault-prod",
            secret_fields_set: ["access_key"],
          },
        }),
      });
      await screen.findByDisplayValue("vault-prod");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(
          JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}").storage_provider_config,
        ).not.toHaveProperty("access_key"),
      );
    });

    it("sends a credential the operator retyped", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        config: anS3Config({
          storage_provider_config: {
            provider: "s3",
            bucket: "vault-prod",
            secret_fields_set: ["access_key"],
          },
        }),
      });
      const key = await screen.findByPlaceholderText("Stored — leave blank to keep");
      await user.type(key, "not-a-real-key");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          storage_provider_config: { access_key: "not-a-real-key" },
        }),
      );
    });

    it("sends the SFTP host key the operator entered", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderCard({
        config: aVaultConfig({
          storage_provider: "sftp",
          storage_provider_config: {
            provider: "sftp",
            host: "nas.example.test",
            port: 22,
            username: "printstash",
          },
        }),
      });
      const hostKey = await screen.findByLabelText("Host key");
      await user.type(hostKey, "nas.example.test ssh-ed25519 AAAA");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          storage_provider: "sftp",
          storage_provider_config: {
            provider: "sftp",
            host_key: "nas.example.test ssh-ed25519 AAAA",
          },
        }),
      );
    });

    it("confirms the save landed", async () => {
      const user = userEvent.setup();
      renderCard();
      await screen.findByDisplayValue("/data/files");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      expect(await screen.findByText("Saved")).toBeInTheDocument();
    });

    it("surfaces a configuration the server refused", async () => {
      // A rejected storage change that reads as saved is how somebody restarts
      // into a vault pointed at a directory that does not exist.
      const user = userEvent.setup();
      renderCard({
        routes: { "PUT /api/v1/config": json({ detail: "data_dir_not_writable" }, 422) },
      });
      await screen.findByDisplayValue("/data/files");

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      expect(await screen.findByText(/422/)).toBeInTheDocument();
    });
  });

  describe("a visitor with no session", () => {
    it("offers no way to save", async () => {
      renderCard({ auth: adminSession({ user: null }) });

      await screen.findByText("Sign in to modify configuration.");
      expect(screen.queryByRole("button", { name: /Save configuration/ })).toBeNull();
    });

    it("says why", async () => {
      renderCard({ auth: adminSession({ user: null }) });

      expect(await screen.findByText("Sign in to modify configuration.")).toBeInTheDocument();
    });
  });

  describe("a configuration that cannot be read", () => {
    it("still renders storage controls", async () => {
      // This card is where an operator goes when storage is misbehaving; an
      // error page instead of a form takes away the fix.
      renderCard({ routes: { "GET /api/v1/config": json({ detail: "boom" }, 500) } });

      expect(await screen.findByText("Storage category")).toBeInTheDocument();
    });

    it("does not expose the legacy S3 backup destination", async () => {
      renderCard();
      await screen.findByDisplayValue("/data/files");

      expect(screen.queryByText("Legacy S3 backup destination")).toBeNull();
      expect(screen.queryByPlaceholderText("my-backup-bucket")).toBeNull();
    });
  });
});

describe("Configured Vault migration entry", () => {
  it("keeps the active provider tier visible when location changes require migration", async () => {
    renderCard({ migrationManaged: true, config: anS3Config() });
    expect(await screen.findByText("Storage safety: Guarded")).toBeVisible();
    expect(screen.queryByText("Expected: Guarded")).toBeNull();
    expect(screen.queryByText("Support: Stable")).toBeNull();
  });
  it("shows a concise current storage summary", async () => {
    renderCard({ migrationManaged: true });
    expect(await screen.findByText("Local disk")).toBeVisible();
    expect(screen.getByText("Storage safety: Verified")).toBeVisible();
    expect(screen.getByText("/data/files")).toBeVisible();
    expect(screen.getByText("/data/thumbs")).toBeVisible();
    expect(screen.queryByText("Verified on local filesystems with working hardlinks.")).toBeNull();
  });
  it("shows configured local paths when the provider has no path overrides", async () => {
    renderCard({
      migrationManaged: true,
      config: aVaultConfig({ storage_provider_config: { provider: "local" } }),
    });
    const details = await screen.findByRole("region", { name: "Storage connection details" });
    expect(within(details).getByText("/data/files")).toBeVisible();
    expect(within(details).getByText("/data/thumbs")).toBeVisible();
    expect(within(details).queryByText("Root")).toBeNull();
  });
  it.each([
    {
      backend: "local" as const,
      label: "Local disk",
      location: "/data/files",
      config: aVaultConfig(),
    },
    {
      backend: "s3" as const,
      label: "Amazon S3",
      location: "vault-prod",
      config: anS3Config({ s3_bucket: "vault-prod" }),
    },
  ])("shows legacy $backend storage paths", async ({ backend, label, location, config }) => {
    renderCard({
      migrationManaged: true,
      config: {
        ...config,
        storage_backend: backend,
        storage_provider: "",
        storage_provider_config: {},
      },
    });
    expect(await screen.findByText(label)).toBeVisible();
    expect(screen.getByText(location)).toBeVisible();
  });
  it("keeps guarded deletion consequences visible when location changes require migration", async () => {
    renderCard({ migrationManaged: true, config: anS3Config() });
    expect(await screen.findByText("Guarded storage consequences")).toBeVisible();
    expect(screen.getByText("Object versions guard destructive operations.")).toBeVisible();
    expect(screen.getByText("Confirmed catalog removal retains stored bytes.")).toBeVisible();
    expect(screen.getByText("Automatic physical deletion is unavailable.")).toBeVisible();
  });
  it("routes location changes through the verified migration flow", async () => {
    renderCard({ migrationManaged: true });
    expect(await screen.findByRole("button", { name: "Move storage" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save configuration" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Data directory")).not.toBeInTheDocument();
  });
  it("hides the migration entry without an administrator session", async () => {
    renderCard({ migrationManaged: true, auth: adminSession({ user: null }) });
    await screen.findByText("Sign in to modify configuration.");
    expect(screen.queryByRole("button", { name: "Move storage" })).not.toBeInTheDocument();
  });
  it("keeps current credentials editable without exposing location fields", async () => {
    renderCard({ migrationManaged: true, config: anS3Config() });
    expect(await screen.findByLabelText(/Access key/)).toBeVisible();
    expect(screen.queryByLabelText("Bucket")).not.toBeInTheDocument();
    expect(screen.getByText("Bucket", { selector: "dt" })).toBeVisible();
    expect(screen.queryByText("/data/files")).not.toBeInTheDocument();
  });
});

describe("Storage configuration ownership", () => {
  it("cancels an abandoned storage configuration read", async () => {
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderCard({
      routes: {
        "GET /api/v1/config": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
      },
    });
    await waitFor(() => expect(signal).toBeDefined());
    app.unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => held.resolve(json(aVaultConfig())));
  });
  it("exposes failed configuration reads with explicit recovery", async () => {
    const app = renderCard({
      routes: { "GET /api/v1/config": () => json({ detail: "unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save configuration" })).toBeNull();
    app.route({ "GET /api/v1/config": () => json(aVaultConfig()) });
    await userEvent.click(screen.getByRole("button", { name: "Retry storage configuration" }));
    expect(await screen.findByDisplayValue("/data/files")).toBeVisible();
  });
  it("publishes the normalized storage receipt to configuration observers", async () => {
    const receipt = aVaultConfig({
      edit_version: 2,
      storage_provider_config: {
        provider: "local",
        data_dir: "/normalized/files",
        thumb_dir: "/data/thumbs",
      },
    });
    const app = renderCard({ routes: { "PUT /api/v1/config": () => json(receipt) } });
    await screen.findByDisplayValue("/data/files");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await screen.findByText("Saved");
    expect(app.client.getQueryData(queryKeys.vaultConfig)).toEqual(receipt);
    expect(
      app
        .requests()
        .filter((request) => request.method === "GET" && request.url === "/api/v1/config"),
    ).toHaveLength(1);
  });
  it("keeps private configuration unreadable without an administrator", async () => {
    const app = renderCard({ auth: adminSession({ user: null }) });
    await screen.findByText("Sign in to modify configuration.");
    expect(app.requests().some((request) => request.url === "/api/v1/config")).toBe(false);
    expect(screen.queryByDisplayValue("/data/files")).toBeNull();
  });
  it("hides private storage configuration after denial", async () => {
    const app = renderCard({ migrationManaged: true });
    await screen.findByText("/data/files");
    app.route({ "GET /api/v1/config": () => json({ detail: "forbidden" }, 403) });
    await act(async () => {
      await app.client.fetchQuery({ ...vaultConfigOptions(), staleTime: 0 }).catch(() => undefined);
    });
    await waitFor(() => expect(screen.queryByText("/data/files")).toBeNull());
  });
  it.each([412, 503])("%s: reviews a storage conflict before revised save", async (status) => {
    let reads = 0;
    const writes: Headers[] = [];
    const latest = aVaultConfig({
      edit_version: 2,
      storage_provider_config: {
        provider: "local",
        data_dir: "/remote/files",
        thumb_dir: "/remote/thumbs",
      },
    });
    const app = renderCard({
      routes: {
        "GET /api/v1/config": () => json(++reads === 1 ? aVaultConfig() : latest),
        "PUT /api/v1/config": (_url, init) => {
          writes.push(new Headers(init?.headers));
          return writes.length === 1
            ? json({ detail: "edit_conflict" }, status)
            : json(aVaultConfig({ edit_version: 3 }));
        },
      },
    });
    const field = await screen.findByLabelText("Data directory");
    await userEvent.clear(field);
    await userEvent.type(field, "/draft/files");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
    const reviewed = await screen.findByRole("region", { name: "Latest saved version" });
    expect(within(reviewed).getByText("/remote/files")).toBeVisible();
    expect(field).toHaveValue("/draft/files");
    expect(screen.getByLabelText("Thumbnail directory")).toHaveValue("/data/thumbs");
    expect(writes).toHaveLength(1);
    await userEvent.click(
      screen.getByRole("button", { name: "Save my draft against this version" }),
    );
    await screen.findByText("Saved");
    expect(writes[0].get("If-Match")).toBe(`"vault-config-e${latest.edit_epoch}-v1"`);
    expect(writes[1].get("If-Match")).toBe(`"vault-config-e${latest.edit_epoch}-v2"`);
    expect(JSON.parse(app.requestsWithMethod("PUT")[1].body).storage_provider_config).toMatchObject(
      { data_dir: "/draft/files", thumb_dir: "/remote/thumbs" },
    );
    expect(app.client.getMutationCache().getAll()).toHaveLength(0);
  });
  it("adopts the reviewed storage configuration without writing", async () => {
    let reads = 0;
    const app = renderCard({
      routes: {
        "GET /api/v1/config": () =>
          json(
            ++reads === 1
              ? aVaultConfig()
              : aVaultConfig({
                  edit_version: 2,
                  storage_provider_config: {
                    provider: "local",
                    data_dir: "/remote/files",
                    thumb_dir: "/data/thumbs",
                  },
                }),
          ),
        "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.type(await screen.findByLabelText("Data directory"), "-draft");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
    await userEvent.click(await screen.findByRole("button", { name: "Use latest version" }));
    expect(screen.getByLabelText("Data directory")).toHaveValue("/remote/files");
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });
  it.each([403, 503])(
    "%s: blocks storage retry when latest configuration cannot be read",
    async (status) => {
      let reads = 0;
      const app = renderCard({
        config: anS3Config(),
        migrationManaged: true,
        routes: {
          "GET /api/v1/config": () =>
            ++reads === 1 ? json(anS3Config()) : json({ detail: "unavailable" }, status),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      await userEvent.type(await screen.findByLabelText(/Access key/), "FakePrivateDraft");
      await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await screen.findByRole("button", { name: "Retry storage configuration" });
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(screen.queryByDisplayValue("FakePrivateDraft") !== null).toBe(status === 503);
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    },
  );
  it("retires a pending storage review on logout", async () => {
    let reads = 0;
    const pending = Promise.withResolvers<Response>();
    const app = renderCard({
      config: anS3Config(),
      migrationManaged: true,
      routes: {
        "GET /api/v1/config": () => (++reads === 1 ? json(anS3Config()) : pending.promise),
        "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
      },
    });
    await userEvent.type(await screen.findByLabelText(/Access key/), "FakePrivateDraft");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
    await act(async () => {
      clearLogin();
      pending.resolve(json(anS3Config({ edit_version: 2 })));
    });
    expect(screen.queryByDisplayValue("FakePrivateDraft")).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Latest saved version" })).not.toBeInTheDocument();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });
  it("retains a storage draft during background configuration refresh", async () => {
    const sent: Headers[] = [];
    const app = renderCard({
      routes: {
        "PUT /api/v1/config": (_url, init) => {
          sent.push(new Headers(init?.headers));
          return json({ detail: "edit_conflict" }, 412);
        },
      },
    });
    const input = await screen.findByLabelText("Data directory");
    await userEvent.clear(input);
    await userEvent.type(input, "/draft/files");
    act(() =>
      app.client.setQueryData(
        queryKeys.vaultConfig,
        aVaultConfig({
          edit_version: 2,
          storage_provider_config: {
            provider: "local",
            data_dir: "/other/files",
            thumb_dir: "/other/thumbs",
          },
        }),
      ),
    );
    expect(screen.getByLabelText("Data directory")).toHaveValue("/draft/files");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    expect(await screen.findByRole("button", { name: "Review latest version" })).toBeVisible();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    expect(sent[0].get("If-Match")).toBe(`"vault-config-e${aVaultConfig().edit_epoch}-v1"`);
    expect(
      JSON.parse(app.requestsWithMethod("PUT")[0].body).storage_provider_config.thumb_dir,
    ).toBe("/data/thumbs");
  });
  it("preserves a newer credential draft after an older save finishes", async () => {
    const held = Promise.withResolvers<Response>();
    const app = renderCard({
      migrationManaged: true,
      config: anS3Config(),
      routes: { "PUT /api/v1/config": () => held.promise },
    });
    const input = await screen.findByLabelText(/Access key/);
    await userEvent.type(input, "test-old-secret");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    await userEvent.clear(input);
    await userEvent.type(input, "test-new-secret");
    await act(async () => held.resolve(json(anS3Config({ edit_version: 2 }))));
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Save configuration" })).toBeEnabled(),
    );
    expect(screen.getByLabelText(/Access key/)).toHaveValue("test-new-secret");
    const retryHeaders: Headers[] = [];
    app.route({
      "PUT /api/v1/config": (_url, init) => {
        retryHeaders.push(new Headers(init?.headers));
        return json(anS3Config({ edit_version: 3 }));
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await screen.findByText("Saved");
    expect(retryHeaders[0].get("If-Match")).toBe(`"vault-config-e${anS3Config().edit_epoch}-v2"`);
  });
  it("retires root enrollment review with its session", async () => {
    const app = renderCard({
      storageHealth: {
        ok: false,
        provider: "local",
        tier: "guarded",
        diagnostics: { root_bindings: { data: "binding_missing" } },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review and enroll" }));
    act(() => clearLogin());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("dismisses enrollment after a server-side root change", async () => {
    const app = renderCard({
      storageHealth: {
        ok: false,
        provider: "local",
        tier: "guarded",
        diagnostics: { root_bindings: { data: "binding_missing" } },
      },
      routes: {
        "POST /api/v1/config/storage-roots/enroll": json({ detail: "storage_review_changed" }, 409),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review and enroll" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Enroll root" }),
    );
    expect(await screen.findByText(/changed during review/)).toBeVisible();
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("refuses enrollment after the reviewed root changes", async () => {
    const app = renderCard({
      storageHealth: {
        ok: false,
        provider: "local",
        tier: "guarded",
        diagnostics: { root_bindings: { data: "binding_missing" } },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review and enroll" }));
    act(() =>
      app.client.setQueryData(queryKeys.vaultConfig, aVaultConfig({ data_dir: "/different/root" })),
    );
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Enroll root" }),
    );
    expect(await screen.findByText(/changed during review/)).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(0);
  });
  it("updates current storage after a confirmed migration cutover", async () => {
    let config = aVaultConfig();
    const run = aVaultMigration({
      state: "ready",
      destination: { provider: "local", data_dir: "/active/files", thumb_dir: "/active/thumbs" },
    });
    renderApp(
      <>
        <StorageConfigCard migrationManaged />
        <VaultMigrationPanel />
      </>,
      {
        routes: {
          "GET /api/v1/config": () => json(config),
          "GET /api/v1/storage/providers": () => json(PROVIDERS),
          "GET /api/v1/backups/sources": () => json([aMigrationBackup()]),
          "GET /api/v1/storage/migrations": () => json([run]),
          "GET /api/v1/storage/migrations/migration-1/report": () =>
            json({ ...run, resource_kind_totals: [], recent_failures: [] }),
          "POST /api/v1/storage/migrations/migration-1/cutover": () => {
            config = aVaultConfig({
              data_dir: "/active/files",
              thumb_dir: "/active/thumbs",
              storage_provider_config: run.destination,
            });
            return json({ ...run, state: "active" });
          },
        },
      },
    );
    await screen.findByText("/data/files");
    await userEvent.click(await screen.findByRole("button", { name: "Switch Vault storage" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Switch Vault storage" }),
    );
    expect(await screen.findByText("/active/files", { exact: true })).toBeVisible();
    expect(screen.queryByText("/data/files", { exact: true })).toBeNull();
  });
  it("discards a conflicting storage draft before renewed review", async () => {
    const app = renderCard({
      routes: { "PUT /api/v1/config": json(aVaultConfig({ edit_version: 3 })) },
    });
    await userEvent.type(await screen.findByLabelText("Data directory"), "-draft");
    act(() =>
      app.client.setQueryData(
        queryKeys.vaultConfig,
        aVaultConfig({
          edit_version: 2,
          storage_provider_config: {
            provider: "local",
            data_dir: "/other/files",
            thumb_dir: "/data/thumbs",
          },
        }),
      ),
    );
    await userEvent.click(await screen.findByRole("button", { name: "Discard storage draft" }));
    expect(screen.getByLabelText("Data directory")).toHaveValue("/other/files");
    await userEvent.click(screen.getByRole("button", { name: "Save configuration" }));
    await screen.findByText("Saved");
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body).storage_provider_config.data_dir).toBe(
      "/other/files",
    );
  });
  it("permits retry after refused root enrollment", async () => {
    let attempts = 0;
    const app = renderCard({
      storageHealth: {
        ok: false,
        provider: "local",
        tier: "guarded",
        diagnostics: { root_bindings: { data: "binding_missing" } },
      },
      routes: {
        "POST /api/v1/config/storage-roots/enroll": () =>
          ++attempts === 1
            ? json({ detail: "storage_root_enrollment_failed" }, 409)
            : json({ enrolled: true, role: "data", restart_required: true }),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review and enroll" }));
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Enroll root" }),
    );
    await screen.findByText(/Something went wrong/);
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Enroll root" }),
    );
    expect(await screen.findByText(/Storage root enrolled/i)).toBeVisible();
    expect(app.requestsWithMethod("POST")).toHaveLength(2);
  });

  it("hides storage after enrollment access is revoked", async () => {
    const app = renderCard({
      migrationManaged: true,
      storageHealth: {
        ok: false,
        provider: "local",
        tier: "guarded",
        diagnostics: { root_bindings: { data: "binding_missing" } },
      },
      routes: {
        "POST /api/v1/config/storage-roots/enroll": () => json({ detail: "forbidden" }, 403),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Review and enroll" }));
    app.route({ "GET /api/v1/config": () => json({ detail: "forbidden" }, 403) });
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Enroll root" }),
    );
    await waitFor(() => expect(screen.queryByText("/data/files", { exact: true })).toBeNull());
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });
});
