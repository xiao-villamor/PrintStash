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
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StorageConfigCard } from "@/components/storage-config-card";
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

function aConfig(over: Partial<VaultConfigRead> = {}): VaultConfigRead {
  return {
    storage_backend: "local",
    storage_provider: "local",
    storage_provider_config: {
      provider: "local",
      data_dir: "/data/files",
      thumb_dir: "/data/thumbs",
    },
    storage_tier: "verified",
    storage_warnings: [],
    storage_unverified_acknowledged: false,
    data_dir: "/data/files",
    thumb_dir: "/data/thumbs",
    s3_bucket: "",
    s3_endpoint_url: "",
    s3_region: "auto",
    s3_access_key: "",
    s3_secret_key: "",
    has_s3_access_key: false,
    has_s3_secret_key: false,
    backup_retention_days: 30,
    trash_retention_days: 30,
    backup_s3_bucket: "",
    backup_s3_endpoint_url: "",
    backup_s3_region: "auto",
    backup_s3_access_key: "",
    backup_s3_secret_key: "",
    has_backup_s3_access_key: false,
    has_backup_s3_secret_key: false,
    has_backup_s3: false,
    auto_mark_known_good: true,
    external_libraries_enabled: false,
    currency: "USD",
    model_thumbnail_width: 640,
    oidc_enabled: false,
    oidc_issuer_url: "",
    oidc_client_id: "",
    has_oidc_client_secret: false,
    oidc_scopes: "openid profile email",
    oidc_username_claim: "preferred_username",
    oidc_groups_claim: "groups",
    oidc_admin_groups: "",
    oidc_display_name: "",
    oidc_redirect_uri: "",
    oidc_allow_insecure_http: false,
    ...over,
  };
}

function anS3Config(over: Partial<VaultConfigRead> = {}): VaultConfigRead {
  return aConfig({
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
  options: RenderAppOptions & { config?: VaultConfigRead; storageHealth?: StorageHealthRead } = {},
) {
  const { config = aConfig(), storageHealth, routes = {}, ...rest } = options;
  return renderApp(<StorageConfigCard storageHealth={storageHealth} />, {
    routes: {
      "GET /api/v1/config": json(config),
      "GET /api/v1/storage/providers": json(PROVIDERS),
      "PUT /api/v1/config": json(config),
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

describe("StorageConfigCard", () => {
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

      await user.click(screen.getByRole("button", { name: /Save configuration/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          storage_provider: "s3",
          storage_provider_config: { provider: "s3" },
        }),
      );
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
        config: aConfig({
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

      await screen.findByDisplayValue("/data/files");
      expect(screen.queryByRole("button", { name: /Save configuration/ })).toBeNull();
    });

    it("says why", async () => {
      renderCard({ auth: adminSession({ user: null }) });

      expect(await screen.findByText("Sign in to modify configuration.")).toBeInTheDocument();
    });
  });

  describe("a configuration that cannot be read", () => {
    it("still renders the backup form", async () => {
      // This card is where an operator goes when storage is misbehaving; an
      // error page instead of a form takes away the fix.
      renderCard({ routes: { "GET /api/v1/config": json({ detail: "boom" }, 500) } });

      expect(await screen.findByPlaceholderText("my-backup-bucket")).toBeInTheDocument();
    });
  });
});
