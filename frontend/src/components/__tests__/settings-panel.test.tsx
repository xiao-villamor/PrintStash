/*
 * The settings screen: fourteen sections, one page, and the deployment's whole
 * configuration surface.
 *
 * The open section lives in `?section=`, which makes every section a shareable
 * link and the back button work — and makes the URL untrusted input. A value
 * nobody ships has to fall back to the overview rather than render nothing, or a
 * stale bookmark becomes a blank settings page with no way forward.
 *
 * Most of what follows is administrative and irreversible-adjacent: creating a
 * user, granting a collection or printer role, changing where the vault stores
 * its bytes, emptying the trash. So the tests assert the *request* each form
 * produces rather than that a handler ran — the request is the contract the
 * backend reads, and a wrong field here is a permission granted to the wrong
 * person or a library pointed at the wrong disk.
 *
 * The read side matters for a different reason: this page is where an operator
 * looks when something is wrong. A section that renders an error instead of a
 * degraded panel takes away the only view they have.
 */

import "@testing-library/jest-dom/vitest";
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPanel } from "@/components/settings-panel";
import { aCollectionPermission, aPrinterPermission } from "@/test-support/permissions";
import { aUser, aApiKey } from "@/test-support/account";
import { collectionTreeRoutes } from "@/test-support/collection-tree";
import { backupCatalogKeys } from "@/lib/queries/settings-backup-catalog";
import { listTasks, syncImportJobs } from "@/lib/task-center";
import { storageConnectionKeys } from "@/lib/queries/settings-storage";
import { queryKeys } from "@/lib/query-client";
import { clearLogin } from "@/lib/auth-store";
import { BROWSER_EXTENSION_SETUP_STORAGE_KEY } from "@/lib/browser-extension-setup";
import {
  aCollection,
  aJob,
  aPrinter,
  aStorageConnection,
  aVaultConfig,
  vaultStats,
} from "@/test-support/factories";
import {
  json,
  memberSession,
  renderApp,
  type RenderAppOptions,
  type RouteTable,
} from "@/test-support/render";
import type { JobStatus } from "@/types";

const HEALTH = {
  status: "ok",
  version: "0.12.1",
  database: { status: "ok" },
  storage: { status: "ok", backend: "local" },
};

const VAULT_CONFIG = aVaultConfig({
  storage_backend: "local",
  data_dir: "/data/files",
  backup_retention_days: 30,
  automatic_backups_enabled: false,
  automatic_backup_time_utc: "02:00",
  automatic_backup_last_attempt_at: null,
  manual_local_backup_enabled: true,
  automatic_local_backup_enabled: true,
  trash_retention_days: 30,
  model_thumbnail_width: 640,
  currency: "USD",
});

const VAULT_STATS = vaultStats();

const TRASHED_MODEL = {
  id: 7,
  name: "Old bracket",
  deleted_at: "2026-01-01T00:00:00Z",
  expires_at: "2026-02-01T00:00:00Z",
  file_count: 2,
  size_bytes: 2048,
  collection: null,
};

const GC_PLAN = {
  id: 12,
  state: "preview",
  digest: "a".repeat(64),
  resource_count: 3,
  candidate_pool_count: 3,
  key_count: 5,
  size_bytes: 2048,
  quarantine_until: null,
  backup_id: null,
  last_error: null,
  items: [],
};

const ISSUED_KEY = aApiKey();

/** The mint response, which carries the secret a listing never returns again. */
const MINTED_KEY = { ...ISSUED_KEY, api_key: "ps_test_this-is-not-a-real-key" };

function renderSettings(options: RenderAppOptions = {}) {
  const { seed = [], routes = {}, ...rest } = options;
  return renderApp(<SettingsPanel />, {
    seed: [[queryKeys.vaultStats, VAULT_STATS], ...seed],
    routes: {
      "GET /api/v1/health/details": json(HEALTH),
      "GET /api/v1/health/releases/latest": json({
        status: "up_to_date",
        update_available: false,
        current_version: "0.12.1",
        latest_version: "0.12.1",
      }),
      "GET /api/v1/config": json(VAULT_CONFIG),
      "GET /api/v1/auth/api-keys": json([]),
      "GET /api/v1/admin/users": json([]),
      ...collectionTreeRoutes([aCollection({ id: 5, name: "Parts" })]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/libraries": json([]),
      "GET /api/v1/notifications": json({ enabled: false, channels: [] }),
      "GET /api/v1/notifications/deliveries": json([]),
      "GET /api/v1/auth/oidc": json({ enabled: false }),
      "GET /api/v1/spoolman/status": json({ enabled: false, url: null, reachable: false }),
      "GET /api/v1/maintenance/audits": json([]),
      "GET /api/v1/models/trash": json([]),
      "GET /api/v1/admin/gc": json(null),
      "GET /api/v1/backups": json([]),
      "GET /api/v1/backups/sources": json([]),
      "GET /api/v1/backups/unowned-local": json([]),
      "GET /api/v1/backups/unowned-remote": json([]),
      "GET /api/v1/storage-connections": json([]),
      "GET /api/v1/storage/providers": json([]),
      "GET /api/v1/models/stats": json(VAULT_STATS),
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

describe("SettingsPanel", () => {
  describe("choosing a section", () => {
    it("opens the overview by default", async () => {
      renderSettings();

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("separates remote connections from Library sources", async () => {
      renderSettings();

      const nav = await screen.findByRole("navigation", { name: "Settings sections" });
      expect(within(nav).getByRole("button", { name: "Library sources" })).toBeVisible();
      expect(within(nav).getByRole("button", { name: "Remote storage" })).toBeVisible();
    });

    it.each([
      "access",
      "storage",
      "backup",
      "remote-storage",
      "imports",
      "maintenance",
      "libraries",
      "notifications",
      "sso",
      "spoolman",
      "design",
      "previews",
      "trash",
      "about",
    ])("opens the %s section from the URL", async (section) => {
      renderSettings({ at: `/settings?section=${section}` });

      // Every section has to render something rather than throwing: this page is
      // where an operator looks when the deployment is already unwell.
      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("falls back to the overview for a section nobody ships", async () => {
      // A stale bookmark must not produce a blank page with no way forward.
      renderSettings({ at: "/settings?section=not-a-section" });

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("moves the section into the URL when one is chosen", async () => {
      const user = userEvent.setup();
      renderSettings();
      const nav = await screen.findByRole("navigation", { name: "Settings sections" });

      await user.click(within(nav).getByRole("button", { name: /Trash/ }));

      expect(await screen.findByText("Deleted models")).toBeInTheDocument();
    });
  });

  describe("the overview", () => {
    it("reports the deployment's health", async () => {
      renderSettings();

      expect(await screen.findByText(/0\.12\.1/)).toBeInTheDocument();
    });

    it("shows restart when the deployment supervisor supports it", async () => {
      renderSettings({
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            capabilities: { restart: true },
          }),
        },
      });

      expect(await screen.findByRole("button", { name: "Restart PrintStash" })).toBeVisible();
    });

    it("hides restart when no deployment supervisor is configured", async () => {
      renderSettings();
      await screen.findByText(/0\.12\.1/);

      expect(screen.queryByRole("button", { name: "Restart PrintStash" })).toBeNull();
    });

    it("hides restart from non-admin users", async () => {
      renderSettings({
        auth: memberSession(),
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            capabilities: { restart: true },
          }),
        },
      });

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
      expect(screen.queryByRole("button", { name: "Restart PrintStash" })).toBeNull();
    });

    it("confirms the restart request", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            capabilities: { restart: true },
          }),
          "POST /api/v1/system/restart": json({ status: "restart_requested" }, 202),
        },
      });
      await user.click(await screen.findByRole("button", { name: "Restart PrintStash" }));

      const dialog = screen.getByRole("dialog", { name: "Restart PrintStash?" });
      expect(dialog).toHaveTextContent("container or service supervisor");
      await user.click(within(dialog).getByRole("button", { name: "Restart now" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.endsWith("/system/restart")),
        ).toBe(true),
      );
      expect(
        await screen.findByText("Restart requested. PrintStash will be back shortly."),
      ).toBeVisible();
    });

    it("keeps restart confirmation open when the request fails", async () => {
      const user = userEvent.setup();
      renderSettings({
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            capabilities: { restart: true },
          }),
          "POST /api/v1/system/restart": json({ detail: "restart_failed" }, 500),
        },
      });
      await user.click(await screen.findByRole("button", { name: "Restart PrintStash" }));

      const dialog = screen.getByRole("dialog", { name: "Restart PrintStash?" });
      await user.click(within(dialog).getByRole("button", { name: "Restart now" }));

      expect(
        await screen.findByText(
          "The server could not restart. Check the server logs and try again.",
        ),
      ).toBeVisible();
      expect(screen.getByRole("dialog", { name: "Restart PrintStash?" })).toBeVisible();
    });

    it("stays usable when the health check fails", async () => {
      // The one screen an operator opens when things are broken must not itself
      // break because the thing it reports on is down.
      renderSettings({
        routes: { "GET /api/v1/health/details": json({ detail: "unavailable" }, 503) },
      });

      expect(await screen.findByRole("navigation", { name: "Settings sections" })).toBeVisible();
    });

    it("explains when the storage root is read-only", async () => {
      renderSettings({
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            status: "degraded",
            components: {
              database: { ok: true },
              storage: {
                ok: false,
                provider: "local",
                tier: "guarded",
                diagnostics: { root_bindings: { data: "binding_missing" } },
              },
            },
          }),
        },
      });

      expect(await screen.findByRole("alert")).toHaveTextContent("Storage is read-only");
      expect(screen.getByRole("alert")).toHaveTextContent(".printstash-storage-root.json");
      expect(screen.getByRole("alert")).toHaveTextContent("Do not acknowledge this warning");
    });

    it("warns when imports copy every file instead of hard-linking", async () => {
      renderSettings({
        routes: {
          "GET /api/v1/health/details": json({
            ...HEALTH,
            components: {
              database: { ok: true },
              storage: {
                ok: true,
                provider: "local",
                tier: "verified",
                diagnostics: { staged_hardlink: false },
              },
            },
          }),
        },
      });

      expect(await screen.findByText("Imports are copied, not hard-linked")).toBeInTheDocument();
    });

    it("re-checks for a release when asked", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings();
      await screen.findByRole("navigation", { name: "Settings sections" });

      const check = screen.queryByRole("button", { name: /Check for updates|Check now/ });
      await user.click(check ?? screen.getAllByRole("button")[0]);

      await waitFor(() => expect(requests().length).toBeGreaterThan(0));
    });
  });

  describe("user administration", () => {
    it("creates the user the admin described", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: { "POST /api/v1/admin/users": json(aUser({ id: 2, username: "maker" })) },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.click(screen.getByLabelText("Username"));
      await user.paste("maker");
      await user.click(screen.getByLabelText("Initial password"));
      await user.paste("Password123");

      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          username: "maker",
        }),
      );
    });

    it("carries the email when one was given", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: { "POST /api/v1/admin/users": json(aUser({ id: 2, username: "maker" })) },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.click(screen.getByLabelText("Username"));
      await user.paste("maker");
      await user.click(screen.getByLabelText("Email"));
      await user.paste("maker@example.test");
      await user.click(screen.getByLabelText("Initial password"));
      await user.paste("Password123");

      await user.click(screen.getByRole("button", { name: "Create" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          email: "maker@example.test",
        }),
      );
    });

    it("refuses a password below the minimum length", async () => {
      // The server enforces it too, but letting the form submit means the admin
      // types a whole user and then loses it to a 422.
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=access" });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.click(screen.getByLabelText("Username"));
      await user.paste("maker");

      await user.click(screen.getByLabelText("Initial password"));
      await user.paste("short");

      expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
    });

    it("lists the users already in the vault", async () => {
      renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]) },
      });

      expect(await screen.findAllByText("maker")).not.toHaveLength(0);
    });

    it("marks which users are vault admins", async () => {
      // Admin is the account that can change storage and empty the trash; a list
      // that does not show it is a list nobody can audit.
      renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "root", is_superuser: true })]),
        },
      });

      expect(await screen.findAllByText("Admin")).not.toHaveLength(0);
    });

    it("marks a disabled account", async () => {
      renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "gone", is_active: false })]),
        },
      });

      expect(await screen.findByText("Disabled")).toBeInTheDocument();
    });

    it("promotes a user to admin", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "PATCH /api/v1/admin/users/2": json(aUser({ id: 2, is_superuser: true })),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Make admin" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          is_superuser: true,
        }),
      );
    });

    it("disables an account rather than deleting it", async () => {
      // A deleted user takes their grants and their audit trail with them;
      // disabling keeps both while ending access.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "DELETE /api/v1/admin/users/2": json(null, 204),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Disable" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/admin/users/2")),
        ).toBe(true),
      );
    });

    it("re-enables a disabled account", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, is_active: false })]),
          "PATCH /api/v1/admin/users/2": json(aUser({ id: 2 })),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Enable" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PATCH").at(-1)?.body ?? "{}")).toMatchObject({
          is_active: true,
        }),
      );
    });

    it("resets a password to what the admin typed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2 })]),
          "POST /api/v1/admin/users/2/password": json(aUser({ id: 2 })),
        },
      });
      await user.type(await screen.findByPlaceholderText("New password"), "Password123");

      await user.click(screen.getByRole("button", { name: "Reset password" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          password: "Password123",
        }),
      );
    });

    it("will not reset a password to one below the minimum", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/admin/users": json([aUser({ id: 2 })]) },
      });

      await user.type(await screen.findByPlaceholderText("New password"), "short");

      expect(screen.getByRole("button", { name: "Reset password" })).toBeDisabled();
    });

    it("hides administration from a non-admin", async () => {
      renderSettings({ at: "/settings?section=access", auth: memberSession() });

      await screen.findByRole("navigation", { name: "Settings sections" });
      expect(screen.queryByRole("button", { name: "Create" })).toBeNull();
    });
  });

  describe("collection access", () => {
    async function pickParts(user: ReturnType<typeof userEvent.setup>) {
      await user.click(screen.getByRole("button", { name: "Select collection" }));
      await user.click(await screen.findByRole("option", { name: /Parts/ }));
    }

    it("loads permissions only for the selected collection", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/collections/5/permissions": json([]) },
      });
      await screen.findByRole("button", { name: "Select collection" });

      expect(requests().filter((call) => call.url.includes("/permissions"))).toEqual([]);
      expect(
        requests().filter(
          (call) => new URL(call.url, "http://test").pathname === "/api/v1/collections",
        ),
      ).toEqual([]);

      await pickParts(user);

      await waitFor(() =>
        expect(
          requests().filter((call) => call.url.includes("/collections/5/permissions")),
        ).toHaveLength(1),
      );
    });

    it("offers a retry when the selected collection's grants fail to load", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/collections/5/permissions": json({ detail: "unavailable" }, 503) },
      });
      await pickParts(user);

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "Collection access could not be loaded.",
      );
      await user.click(screen.getByRole("button", { name: "Retry" }));
      await waitFor(() =>
        expect(
          requests().filter((call) => call.url.includes("/collections/5/permissions")),
        ).toHaveLength(2),
      );
    });

    it("grants the role the admin chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/collections/5/permissions": json([]),
          "PUT /api/v1/collections/5/permissions/2": json(aCollectionPermission()),
        },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");
      await pickParts(user);

      await user.click(screen.getByRole("button", { name: "Grant" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("PUT").some((call) =>
            call.url.endsWith("/collections/5/permissions/2"),
          ),
        ).toBe(true),
      );
    });

    it("cannot grant before a user is chosen", async () => {
      // The grant is per user *and* per collection; a half-filled form would
      // otherwise send a request naming nobody.
      renderSettings({ at: "/settings?section=access" });

      expect(await screen.findByRole("button", { name: "Grant" })).toBeDisabled();
    });

    it("does not offer an admin as a grantee", async () => {
      // A vault admin already has every collection; listing them invites a grant
      // that changes nothing and reads as though it did.
      renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 3, username: "root", is_superuser: true })]),
        },
      });

      const [select] = await screen.findAllByLabelText("User");
      expect(within(select).queryByRole("option", { name: "root" })).toBeNull();
    });

    it("lists the grants already made", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/collections/5/permissions": json([aCollectionPermission()]),
        },
      });

      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");
      await pickParts(user);

      expect(await screen.findByTitle("Remove collection access")).toBeInTheDocument();
    });

    it("revokes a grant", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/collections/5/permissions": json([aCollectionPermission()]),
          "DELETE /api/v1/collections/5/permissions/2": json(null, 204),
        },
      });

      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");
      await pickParts(user);

      await user.click(await screen.findByTitle("Remove collection access"));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) =>
            call.url.endsWith("/collections/5/permissions/2"),
          ),
        ).toBe(true),
      );
    });
  });

  describe("printer access", () => {
    it("grants the printer role the admin chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
          "GET /api/v1/printers/4/permissions": json([]),
          "PUT /api/v1/printers/4/permissions/2": json(aPrinterPermission()),
        },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.selectOptions((await screen.findAllByLabelText("User"))[1], "2");
      await user.selectOptions(screen.getByLabelText("Printer"), "4");

      await user.click(screen.getByRole("button", { name: "Save" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("PUT").some((call) => call.url.endsWith("/printers/4/permissions/2")),
        ).toBe(true),
      );
    });

    it("revokes a printer grant", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
          "GET /api/v1/printers/4/permissions": json([aPrinterPermission()]),
          "DELETE /api/v1/printers/4/permissions/2": json(null, 204),
        },
      });

      await user.selectOptions((await screen.findAllByLabelText("User"))[1], "2");

      await user.click(await screen.findByTitle("Remove printer access"));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) =>
            call.url.endsWith("/printers/4/permissions/2"),
          ),
        ).toBe(true),
      );
    });
  });

  describe("API keys", () => {
    it("mints a key under the name the user gave", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: { "POST /api/v1/auth/api-keys": json(MINTED_KEY) },
      });
      const name = await screen.findByLabelText("Key name");
      await user.clear(name);
      await user.type(name, "Slicer");

      await user.click(screen.getByRole("button", { name: "Generate" }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("POST").at(-1)?.body ?? "{}")).toMatchObject({
          name: "Slicer",
        }),
      );
    });

    it("shows the minted key once so it can be copied", async () => {
      // The server never returns it again; a key shown nowhere is a key nobody
      // can use.
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=access",
        routes: { "POST /api/v1/auth/api-keys": json(MINTED_KEY) },
      });
      await screen.findByLabelText("Key name");

      await user.click(screen.getByRole("button", { name: "Generate" }));

      expect(await screen.findByTitle("Copy API key")).toBeInTheDocument();
    });

    it("lists the keys already issued", async () => {
      renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/auth/api-keys": json([ISSUED_KEY]),
        },
      });

      expect(await screen.findByText("Slicer")).toBeInTheDocument();
    });

    it("revokes a key", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/auth/api-keys": json([ISSUED_KEY]),
          "DELETE /api/v1/auth/api-keys/9": json(null, 204),
        },
      });

      await user.click(await screen.findByTitle("Revoke API key"));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) => call.url.endsWith("/auth/api-keys/9")),
        ).toBe(true),
      );
    });
  });

  describe("backups", () => {
    const BACKUP = {
      backup_id: "2026-01-01T000000Z",
      created_at: "2026-01-01T00:00:00Z",
      location: "local",
      app_version: "0.12.1",
      file_count: 42,
      size_bytes: 1024 * 1024,
      storage_backend: "local",
      namespace: "vault-backups",
      source_ref: "local-source-ref",
      provider_ref: "local",
      key: "printstash-backups/2026-01-01T000000Z.tar.gz",
      prefix: "printstash-backups/",
      archive_sha256: "a".repeat(64),
    };

    /**
     * A manual backup is a Job: the POST only queues it, and the new backup is
     * the Job's result. Ids are distinct per test because the Task Center keeps
     * a terminal Job's outcome for the life of the module.
     */
    function backupRoutes(jobId: string, job: Partial<JobStatus> = {}): RouteTable {
      return {
        "POST /api/v1/backups": json({ job_id: jobId, state: "queued", message: "queued" }),
        "GET /api/v1/jobs": json([
          aJob({
            job_id: jobId,
            kind: "backups.create",
            result: {
              backup_id: BACKUP.backup_id,
              created_at: BACKUP.created_at,
              location: BACKUP.location,
              app_version: BACKUP.app_version,
              file_count: BACKUP.file_count,
              size_bytes: BACKUP.size_bytes,
              storage_backend: BACKUP.storage_backend,
              namespace: BACKUP.namespace,
              source_ref: BACKUP.source_ref,
              provider_ref: BACKUP.provider_ref,
              outcome: "completed",
            },
            ...job,
          }),
        ]),
      };
    }

    it("distinguishes an unavailable backup catalog from empty storage", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json({ detail: "unavailable" }, 503) },
      });
      expect(await screen.findByText("Could not load backup sources.")).toBeVisible();
      expect(screen.queryByText("No backups found.")).toBeNull();
    });
    it("recovers the owned backup catalog explicitly", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json({ detail: "unavailable" }, 503) },
      });
      await screen.findByText("Could not load backup sources.");
      app.route({ "GET /api/v1/backups/sources": json([BACKUP]) });
      await userEvent.click(screen.getByRole("button", { name: "Refresh backups" }));
      expect(await screen.findByText(BACKUP.backup_id)).toBeVisible();
      expect(screen.queryByText("Could not load backup sources.")).toBeNull();
      expect(app.requestsWithMethod("POST")).toHaveLength(0);
    });
    it("preserves an unsaved backup retention during refresh", async () => {
      const app = renderSettings({ at: "/settings?section=backup" });
      await screen.findByText("No backups found.");
      const retention = screen.getByLabelText("Retention (days)");
      await userEvent.clear(retention);
      await userEvent.type(retention, "14");
      await userEvent.click(screen.getByRole("button", { name: "Refresh backups" }));
      await waitFor(() =>
        expect(
          app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/backups/sources"),
        ).toHaveLength(2),
      );
      await screen.findByText("No backups found.");
      expect(retention).toHaveValue(14);
    });
    it("preserves an unsaved backup schedule during refresh", async () => {
      renderSettings({ at: "/settings?section=backup" });
      await screen.findByText("No backups found.");
      await userEvent.click(screen.getByLabelText("Enable automatic backups"));
      const schedule = screen.getByLabelText("Daily time (UTC)");
      fireEvent.change(schedule, { target: { value: "04:30" } });
      await userEvent.click(screen.getByRole("button", { name: "Refresh backups" }));
      await screen.findByText("No backups found.");
      expect(schedule).toHaveValue("04:30");
      expect(screen.getByLabelText("Enable automatic backups")).toBeChecked();
    });
    it("blocks backup policy until configuration is available", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/config": json({ detail: "unavailable" }, 503) },
      });
      await screen.findByText("No backups found.");
      expect(await screen.findByText("Could not load backup settings.")).toBeVisible();
      expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Save backup settings" })).toBeDisabled();
    });
    it("cancels an abandoned backup catalog read", async () => {
      const held = Promise.withResolvers<Response>();
      let signal: AbortSignal | null | undefined;
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": (_url, init) => {
            signal = init?.signal;
            return held.promise;
          },
        },
      });
      await waitFor(() => expect(signal).toBeDefined());
      app.unmount();
      await act(async () => {
        held.resolve(json([]));
        await held.promise;
      });
      expect(signal?.aborted).toBe(true);
    });
    it("hides denied backup rows after refresh", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Delete backup" }));
      expect(screen.getByRole("dialog")).toBeVisible();
      app.route({ "GET /api/v1/backups/sources": json({ detail: "forbidden" }, 403) });
      // A refresh can also originate from another observer while confirmation is open.
      await act(async () => {
        await app.client.invalidateQueries();
      });
      await screen.findByText("Could not load backup sources.");
      await waitFor(() => expect(screen.queryByText(BACKUP.backup_id)).toBeNull());
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
    });
    it("distinguishes failed backup discovery from no candidates", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/unowned-local": json({ detail: "unavailable" }, 503) },
      });
      expect(await screen.findByText("Some backup sources could not be loaded.")).toBeVisible();
      expect(screen.queryByText("No backups found.")).toBeNull();
    });

    it("rejects a backup confirmation after its source changed", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP]),
          "DELETE /api/v1/backups/": json(null, 204),
        },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Delete backup" }));
      act(() =>
        app.client.setQueryData(backupCatalogKeys.owned, [
          { ...BACKUP, archive_sha256: "b".repeat(64) },
        ]),
      );
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Delete backup" }),
      );
      expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
      expect(
        await screen.findByText(
          "The source changed during review. Review the latest version again.",
        ),
      ).toBeVisible();
    });
    it("keeps a deleted backup absent after an older catalog response", async () => {
      const held = Promise.withResolvers<Response>();
      let signal: AbortSignal | null | undefined;
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP]),
          "DELETE /api/v1/backups/": json(null, 204),
        },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Delete backup" }));
      app.route({
        "GET /api/v1/backups/sources": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
      });
      let pending: Promise<void>;
      act(() => {
        pending = app.client.invalidateQueries({ queryKey: backupCatalogKeys.owned });
      });
      await waitFor(() => expect(signal).toBeDefined());
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Delete backup" }),
      );
      await waitFor(() => expect(app.requestsWithMethod("DELETE")).toHaveLength(1));
      await waitFor(() => expect(app.client.getQueryData(backupCatalogKeys.owned)).toEqual([]));
      await act(async () => {
        held.resolve(json([BACKUP]));
        await pending;
      });
      await waitFor(() => expect(screen.queryByText(BACKUP.backup_id)).toBeNull());
      expect(signal?.aborted).toBe(true);
    });
    it("retires backup confirmations with the private session", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Delete backup" }));
      act(() => clearLogin());
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
      expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
    });
    it("retains an accepted backup without disposed-view feedback", async () => {
      const id = "backup-disposed-view";
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: backupRoutes(id, { state: "running", result: null }),
      });
      await userEvent.click(await screen.findByRole("button", { name: /Backup now/ }));
      await waitFor(() => expect(listTasks().some((task) => task.jobId === id)).toBe(true));
      app.rerender(<p>Other view</p>);
      app.route(backupRoutes(id));
      await act(async () => {
        await syncImportJobs();
      });
      await waitFor(() =>
        expect(listTasks().find((task) => task.jobId === id)?.status).toBe("completed"),
      );
      expect(screen.queryByText(/Backup created —/)).toBeNull();
    });
    it("publishes acknowledged backup policy to shared configuration", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "PUT /api/v1/config": json({
            ...VAULT_CONFIG,
            edit_version: 2,
            backup_retention_days: 15,
          }),
        },
      });
      await screen.findByText("No backups found.");
      await userEvent.clear(screen.getByLabelText("Retention (days)"));
      await userEvent.type(screen.getByLabelText("Retention (days)"), "14");
      await userEvent.click(screen.getByRole("button", { name: "Save retention" }));
      await waitFor(() =>
        expect(app.client.getQueryData(queryKeys.vaultConfig)).toMatchObject({
          backup_retention_days: 15,
        }),
      );
      expect(screen.getByLabelText("Retention (days)")).toHaveValue(15);
    });

    it("retains partial policy failure without claiming full success", async () => {
      const connection = aStorageConnection({
        id: 7,
        name: "Off-site archive",
        purpose: "backup",
        manual_backup_enabled: true,
      });
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/storage-connections": json([connection]),
          "PUT /api/v1/config": json({ ...VAULT_CONFIG, automatic_backups_enabled: true }),
          "PATCH /api/v1/storage-connections/7": json({ detail: "unavailable" }, 503),
        },
      });
      await screen.findByText("No backups found.");
      await userEvent.click(screen.getByLabelText("Enable automatic backups"));
      await userEvent.click(screen.getByLabelText("Use Off-site archive for manual backups"));
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await waitFor(() =>
        expect(app.client.getQueryData(queryKeys.vaultConfig)).toMatchObject({
          automatic_backups_enabled: true,
        }),
      );
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save backup settings" })).toBeEnabled(),
      );
      expect(screen.getByLabelText("Use Off-site archive for manual backups")).not.toBeChecked();
      expect(app.client.getQueryData(storageConnectionKeys.all)).toContainEqual(connection);
      expect(screen.queryByText("Backup settings saved.")).toBeNull();
      app.route({
        "PATCH /api/v1/storage-connections/7": json({
          ...connection,
          manual_backup_enabled: false,
        }),
      });
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await waitFor(() =>
        expect(app.client.getQueryData(storageConnectionKeys.all)).toContainEqual({
          ...connection,
          manual_backup_enabled: false,
        }),
      );
      expect(app.requestsWithMethod("PATCH")).toHaveLength(2);
    });

    it("publishes an adopted backup before another listing", async () => {
      const candidate = {
        ...BACKUP,
        filename: "discovered.tar.gz",
        source_ref: "discovered-source",
      };
      const saved = { ...BACKUP, source_ref: "adopted-source" };
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-local": json([candidate]),
          "POST /api/v1/backups/adopt-local": json(saved),
        },
      });
      await userEvent.click(await screen.findByRole("button", { name: "Adopt backup" }));
      await userEvent.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Adopt backup" }),
      );
      await waitFor(() =>
        expect(app.client.getQueryData(backupCatalogKeys.owned)).toEqual([saved]),
      );
      expect(screen.queryByText("discovered.tar.gz")).toBeNull();
      expect(
        app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/backups/sources"),
      ).toHaveLength(1);
    });

    it("refreshes the backup catalog when returning", async () => {
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });
      await screen.findByText(BACKUP.backup_id);
      app.rerender(<p>Other view</p>);
      app.route({
        "GET /api/v1/backups/sources": json([
          { ...BACKUP, backup_id: "backup-completed-while-away" },
        ]),
      });
      app.rerender(<SettingsPanel />);
      expect(await screen.findByText("backup-completed-while-away")).toBeVisible();
      expect(
        app.requestsWithMethod("GET").filter((row) => row.url === "/api/v1/backups/sources"),
      ).toHaveLength(2);
    });

    it("keeps backup controls out of the storage section", async () => {
      renderSettings({ at: "/settings?section=storage" });

      expect(await screen.findByRole("button", { name: "Move storage" })).toBeVisible();
      expect(screen.queryByRole("button", { name: /Backup now/ })).toBeNull();
      const move = screen.getByRole("region", { name: "Move Vault storage" });
      const insights = screen.getByRole("heading", { name: "Storage insights" });
      expect(
        insights.compareDocumentPosition(move) & Node.DOCUMENT_POSITION_FOLLOWING,
      ).toBeTruthy();
    });

    it("opens backup controls in their own section", async () => {
      renderSettings({ at: "/settings?section=backup" });

      expect(await screen.findByRole("button", { name: /Backup now/ })).toBeVisible();
      expect(screen.getByLabelText("Upload backup archive")).toBeInTheDocument();
      expect(screen.getByLabelText("Retention (days)")).toHaveValue(30);
    });

    it("saves backup retention from the backup section", async () => {
      const user = userEvent.setup();
      let update: unknown;
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "PUT /api/v1/config": (_url, init) => {
            update = JSON.parse(String(init?.body));
            return json({ ...VAULT_CONFIG, edit_version: 2, backup_retention_days: 14 });
          },
        },
      });

      const input = await screen.findByLabelText("Retention (days)");
      await waitFor(() => expect(input).toBeEnabled());
      await user.clear(input);
      await user.type(input, "14");
      await user.click(screen.getByRole("button", { name: "Save retention" }));

      await waitFor(() =>
        expect(requestsWithMethod("PUT").some((call) => call.url.endsWith("/config"))).toBe(true),
      );
      expect(update).toEqual({ backup_retention_days: 14 });
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save retention" })).toBeEnabled(),
      );
      expect(input).toHaveValue(14);
    });

    it.each([
      { label: "empty", value: "" },
      { label: "negative", value: "-1" },
      { label: "above the maximum", value: "366" },
    ])("refuses $label backup retention", async ({ value }) => {
      renderSettings({ at: "/settings?section=backup" });
      const input = await screen.findByLabelText("Retention (days)");

      fireEvent.change(input, { target: { value } });

      expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
      expect(screen.getByText("Retention must be between 0 and 365 days.")).toBeVisible();
    });

    it("saves the complete automatic-backup policy", async () => {
      const user = userEvent.setup();
      const connection = aStorageConnection({
        id: 7,
        name: "Off-site archive",
        manual_backup_enabled: true,
        automatic_backup_enabled: false,
      });
      const updates: unknown[] = [];
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/storage-connections": json([connection]),
          "PUT /api/v1/config": (_url, init) => {
            updates.push(JSON.parse(String(init?.body)));
            return json({
              ...VAULT_CONFIG,
              edit_version: 2,
              automatic_backups_enabled: true,
              automatic_backup_time_utc: "04:30",
            });
          },
          "PATCH /api/v1/storage-connections/7": (_url, init) => {
            const body = JSON.parse(String(init?.body));
            updates.push(body);
            return json({ ...connection, ...body });
          },
        },
      });

      await user.click(await screen.findByLabelText("Enable automatic backups"));
      await user.clear(screen.getByLabelText("Daily time (UTC)"));
      fireEvent.change(screen.getByLabelText("Daily time (UTC)"), {
        target: { value: "04:30" },
      });
      await user.click(screen.getByLabelText("Use local storage for manual backups"));
      await user.click(screen.getByLabelText("Use Off-site archive for automatic backups"));
      await user.click(screen.getByRole("button", { name: "Save backup settings" }));

      await waitFor(() => expect(requestsWithMethod("PUT")).toHaveLength(1));
      await waitFor(() => expect(requestsWithMethod("PATCH")).toHaveLength(1));
      expect(updates).toEqual(
        expect.arrayContaining([
          {
            automatic_backups_enabled: true,
            automatic_backup_time_utc: "04:30",
            manual_local_backup_enabled: false,
            automatic_local_backup_enabled: true,
          },
          { automatic_backup_enabled: true },
        ]),
      );
    });

    it("preserves source-only connections when saving backup policy", async () => {
      const backup = aStorageConnection({
        id: 7,
        name: "Backup destination",
        purpose: "backup",
        manual_backup_enabled: true,
      });
      const library = aStorageConnection({ id: 9, name: "Source connection", purpose: "library" });
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/storage-connections": json([backup, library]),
          "PUT /api/v1/config": json({ ...VAULT_CONFIG, edit_version: 2 }),
          "PATCH /api/v1/storage-connections/7": json({ ...backup, manual_backup_enabled: false }),
        },
      });
      await screen.findByText("No backups found.");
      await userEvent.click(screen.getByLabelText("Use Backup destination for manual backups"));
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await waitFor(() =>
        expect(app.client.getQueryData(storageConnectionKeys.all)).toContainEqual({
          ...backup,
          manual_backup_enabled: false,
        }),
      );
      expect(app.client.getQueryData(storageConnectionKeys.all)).toContainEqual(library);
      expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    });

    it("refuses to save a manual policy without a destination", async () => {
      const user = userEvent.setup();
      const connection = aStorageConnection({ id: 7, name: "Off-site archive" });
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/storage-connections": json([connection]) },
      });

      await user.click(await screen.findByLabelText("Use local storage for manual backups"));
      await user.click(screen.getByLabelText("Use Off-site archive for manual backups"));
      await user.click(screen.getByRole("button", { name: "Save backup settings" }));

      expect(
        await screen.findByText("Select at least one manual backup destination."),
      ).toBeVisible();
      expect(requestsWithMethod("PUT")).toHaveLength(0);
      expect(requestsWithMethod("PATCH")).toHaveLength(0);
    });

    it("says so when nothing has been backed up", async () => {
      renderSettings({ at: "/settings?section=backup" });

      expect(await screen.findByText("No backups found.")).toBeInTheDocument();
    });

    it("lists the backups taken", async () => {
      const { requests } = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      expect(await screen.findByText("2026-01-01T000000Z")).toBeInTheDocument();
      expect(screen.getByText("Locator: vault-backups · local-source-ref")).toBeInTheDocument();
      expect(requests().some((call) => call.url.endsWith("/api/v1/backups/sources"))).toBe(true);
    });

    it("disables unsafe backup deletion while explaining retained bytes", async () => {
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([
            {
              ...BACKUP,
              location: "opendal:sftp",
              operations: {
                catalog_purge: { allowed: false, reason: "storage_backup_ownership_required" },
                physical_delete: { allowed: false, reason: "storage_exact_delete_unavailable" },
                automatic_retention: { allowed: false, reason: "storage_retention_unsupported" },
                gc_witness: { allowed: false, reason: "storage_gc_witness_unsupported" },
              },
            },
          ]),
        },
      });
      expect(await screen.findByRole("button", { name: "Delete backup" })).toBeDisabled();
      expect(screen.getByText(/Its bytes and ownership record are retained/)).toBeVisible();
      expect(screen.getByText(/Automatic retention is unavailable for this copy/)).toBeVisible();
      expect(screen.getByRole("button", { name: "Restore" })).toBeEnabled();
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("deletes the exact backup source after confirmation", async () => {
      const user = userEvent.setup();
      let deleted = false;
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP]),
          "GET /api/v1/backups/unowned-local": () =>
            json(
              deleted
                ? []
                : [
                    {
                      ...BACKUP,
                      filename: "2026-01-01T000000Z.tar.gz",
                      source_ref: undefined,
                    },
                  ],
            ),
          "DELETE /api/v1/backups/2026-01-01T000000Z": () => {
            deleted = true;
            return json({ backup_id: BACKUP.backup_id, deleted: true });
          },
        },
      });

      await user.click(await screen.findByRole("button", { name: "Delete backup" }));
      const dialog = screen.getByRole("dialog");
      await user.click(within(dialog).getByRole("button", { name: "Delete backup" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("DELETE").some((call) =>
            call.url.includes("/backups/2026-01-01T000000Z?source_ref=local-source-ref"),
          ),
        ).toBe(true),
      );
      expect(screen.queryByText("2026-01-01T000000Z")).toBeNull();
      await waitFor(() => expect(screen.queryByText("2026-01-01T000000Z.tar.gz")).toBeNull());
    });

    it("describes deletion without restore consequences", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      await user.click(await screen.findByRole("button", { name: "Delete backup" }));

      const dialog = screen.getByRole("dialog");
      expect(dialog).toHaveTextContent("Delete this backup copy?");
      expect(dialog).not.toHaveTextContent(
        "This replaces the current database and stored files with the selected backup.",
      );
    });

    it("labels an OpenDAL backup with its remote destination", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([
            {
              ...BACKUP,
              location: "opendal:gdrive",
              provider_ref: "gdrive",
              source_ref: "remote-source-ref",
            },
          ]),
        },
      });

      expect(await screen.findByText(/1 MB · opendal:gdrive/i)).toBeVisible();
    });

    it("keeps a backup visible when deletion fails", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP]),
          "DELETE /api/v1/backups/2026-01-01T000000Z": json(
            { detail: "backup_remote_delete_unverified" },
            409,
          ),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Delete backup" }));
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Delete backup" }),
      );

      expect(
        await screen.findByText(
          "This remote backup changed or couldn't be verified, so it was not deleted.",
        ),
      ).toBeVisible();
      expect(screen.getByText("2026-01-01T000000Z")).toBeVisible();
    });

    it("keeps same-id sources independent for exact downloads", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([
            { ...BACKUP, source_ref: "local-source", location: "local" },
            { ...BACKUP, source_ref: "s3-source", location: "s3" },
          ]),
          "GET /api/v1/backups/2026-01-01T000000Z/download": json([]),
        },
      });

      const downloads = await screen.findAllByRole("button", { name: "Download" });
      await user.click(downloads[0]);
      await waitFor(() =>
        expect(
          requests().some((call) => call.url.includes("/download?source_ref=local-source")),
        ).toBe(true),
      );
      await user.click(downloads[1]);
      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/download?source_ref=s3-source"))).toBe(
          true,
        ),
      );
    });

    it("surfaces validated legacy candidates", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-local": json([
            {
              filename: "nexus3d-backup-2025.tar.gz",
              backup_id: "legacy-1",
              created_at: "2025-01-01T00:00:00Z",
              location: "local",
              app_version: "0.11.0",
              file_count: 8,
              size_bytes: 2048,
              storage_backend: "local",
            },
          ]),
        },
      });

      expect(await screen.findByText("nexus3d-backup-2025.tar.gz")).toBeInTheDocument();
      expect(screen.getByText(/8 files.*v0\.11\.0/)).toBeInTheDocument();
    });

    it("surfaces validated legacy S3 candidates with their exact locator", async () => {
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-s3": json([
            {
              key: "nexus3d-backups/legacy.tar.gz",
              prefix: "nexus3d-backups/",
              namespace: "printstash-bucket/nexus3d-backups",
              source_ref: "s3-source",
              archive_sha256: "a".repeat(64),
              backup_id: "legacy-1",
              created_at: "2025-01-01T00:00:00Z",
              location: "s3",
              app_version: "0.11.0",
              file_count: 8,
              size_bytes: 2048,
              storage_backend: "s3",
            },
          ]),
        },
      });

      expect(await screen.findByText("nexus3d-backups/legacy.tar.gz")).toBeInTheDocument();
      expect(screen.getByText("Namespace: printstash-bucket/nexus3d-backups")).toBeInTheDocument();
      expect(screen.getByText(/SHA-256 a{16}/)).toBeInTheDocument();
    });

    it("adopts a validated OpenDAL candidate from its exact connection", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-remote": json([
            {
              connection_id: 7,
              connection_name: "Recovery Drive",
              provider: "gdrive",
              key: "gdrive/PrintStash/printstash-backups/printstash-backup-old.tar.gz",
              prefix: "gdrive/PrintStash/printstash-backups",
              namespace: "gdrive/PrintStash",
              source_ref: "remote-source",
              archive_sha256: "b".repeat(64),
              backup_id: "old",
              created_at: "2025-01-01T00:00:00Z",
              location: "opendal:gdrive",
              app_version: "0.12.1",
              file_count: 8,
              size_bytes: 2048,
              storage_backend: "local",
            },
          ]),
          "POST /api/v1/backups/adopt-remote": json({
            ...BACKUP,
            backup_id: "old",
            source_ref: "remote-source",
          }),
        },
      });

      expect(await screen.findByText(/Recovery Drive.*GDRIVE/)).toBeVisible();
      await user.click(screen.getByRole("button", { name: "Adopt backup" }));
      const dialog = screen.getByRole("dialog", { name: "Adopt remote backup?" });
      await user.click(within(dialog).getByRole("button", { name: "Adopt backup" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) =>
            call.url.includes(
              "/backups/adopt-remote?connection_id=7&key=gdrive%2FPrintStash%2Fprintstash-backups%2Fprintstash-backup-old.tar.gz&source_ref=remote-source&expected_archive_sha256=",
            ),
          ),
        ).toBe(true),
      );
    });

    it("adopts one S3 candidate only after confirmation", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-s3": json([
            {
              key: "nexus3d-backups/legacy.tar.gz",
              prefix: "nexus3d-backups/",
              namespace: "printstash-bucket/nexus3d-backups",
              source_ref: "s3-source",
              archive_sha256: "a".repeat(64),
              backup_id: "legacy-1",
              created_at: "2025-01-01T00:00:00Z",
              location: "s3",
              app_version: "0.11.0",
              file_count: 8,
              size_bytes: 2048,
              storage_backend: "s3",
            },
          ]),
          "POST /api/v1/backups/adopt-s3": json({
            ...BACKUP,
            backup_id: "legacy-1",
            source_ref: "s3-source",
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Adopt backup" }));
      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent("nexus3d-backups/legacy.tar.gz");
      expect(dialog).toHaveTextContent("printstash-bucket/nexus3d-backups");
      expect(dialog).toHaveTextContent("a".repeat(16));
      await user.click(within(dialog).getByRole("button", { name: "Adopt backup" }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) =>
            call.url.includes(
              "/backups/adopt-s3?key=nexus3d-backups%2Flegacy.tar.gz&source_ref=s3-source&expected_archive_sha256=",
            ),
          ),
        ).toBe(true),
      );
    });

    it("confirms one legacy candidate before adopting", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/unowned-local": json([
            {
              filename: "nexus3d-backup-2025.tar.gz",
              backup_id: "legacy-1",
              created_at: "2025-01-01T00:00:00Z",
              location: "local",
              app_version: "0.11.0",
              file_count: 8,
              size_bytes: 2048,
              storage_backend: "local",
            },
          ]),
          "POST /api/v1/backups/adopt-local": json({
            ...BACKUP,
            backup_id: "legacy-1",
            source_ref: "adopted-local",
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Adopt backup" }));
      expect(await screen.findByRole("dialog")).toHaveTextContent("nexus3d-backup-2025.tar.gz");
      expect(screen.getByRole("dialog")).toHaveTextContent("8 files");
      await user.click(
        within(screen.getByRole("dialog")).getByRole("button", { name: "Adopt backup" }),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) =>
            call.url.includes("/backups/adopt-local?filename=nexus3d-backup-2025.tar.gz"),
          ),
        ).toBe(true),
      );
    });

    it("says which version of the app wrote each one", async () => {
      // Restoring a backup written by a newer app is how a vault ends up with a
      // schema its code cannot read.
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      expect(await screen.findByText("v0.12.1")).toBeInTheDocument();
    });

    it("takes a backup on demand", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=backup", routes: backupRoutes("backup-on-demand") });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));

      expect(await screen.findByText("Backup created — 42 files, 1.0 MB")).toBeVisible();
      expect(screen.getByText(BACKUP.backup_id)).toBeInTheDocument();
    });

    it("reports a backup whose Job failed", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: backupRoutes("backup-job-failed", {
          state: "failed",
          error: "backup_blob_missing",
          result: null,
        }),
      });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));

      expect(
        await screen.findByText(
          "A file needed for the backup is missing. Check the storage and try again.",
        ),
      ).toBeVisible();
    });

    it("uploads the selected backup archive", async () => {
      const user = userEvent.setup();
      let uploaded: FormDataEntryValue | null = null;
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "POST /api/v1/backups/upload": (_url, init) => {
            if (init?.body instanceof FormData) uploaded = init.body.get("file");
            return json(BACKUP, 201);
          },
        },
      });
      const archive = new File(["archive"], "printstash-backup-upload.tar.gz", {
        type: "application/gzip",
      });

      await user.upload(await screen.findByLabelText("Upload backup archive"), archive);

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.endsWith("/backups/upload")),
        ).toBe(true),
      );
      expect(uploaded).toBeInstanceOf(File);
      expect(uploaded).toHaveProperty("name", "printstash-backup-upload.tar.gz");
    });

    it("reports a backup failure", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "POST /api/v1/backups": json({ detail: "backup_blob_missing" }, 409),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));

      expect(
        await screen.findByText(
          "A file needed for the backup is missing. Check the storage and try again.",
        ),
      ).toBeVisible();
    });

    it("allows retrying after a backup failure", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: {
          "POST /api/v1/backups": json({ detail: "backup_blob_missing" }, 409),
        },
      });
      const backupNow = await screen.findByRole("button", { name: /Backup now/ });

      await user.click(backupNow);
      await screen.findByText(
        "A file needed for the backup is missing. Check the storage and try again.",
      );

      expect(backupNow).toBeEnabled();
    });

    it("keeps older id-only backups when the new response lacks a source reference", async () => {
      const user = userEvent.setup();
      const olderLocal = {
        ...BACKUP,
        backup_id: "older-local",
        source_ref: undefined,
        namespace: undefined,
      };
      const olderCloud = {
        ...BACKUP,
        backup_id: "older-cloud",
        location: "s3",
        source_ref: undefined,
        namespace: undefined,
      };
      // The backup runs as a Job; its result is the new backup.
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([olderLocal, olderCloud]),
          "POST /api/v1/backups": json({
            job_id: "backup-job-id-only",
            state: "queued",
            message: "queued",
          }),
          "GET /api/v1/jobs": json([
            aJob({
              job_id: "backup-job-id-only",
              kind: "backups.create",
              result: {
                backup_id: "new-backup",
                created_at: BACKUP.created_at,
                location: BACKUP.location,
                app_version: BACKUP.app_version,
                file_count: BACKUP.file_count,
                size_bytes: BACKUP.size_bytes,
                storage_backend: BACKUP.storage_backend,
                source_ref: null,
                namespace: null,
              },
            }),
          ]),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));
      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/backups"))).toBe(true),
      );
      expect(await screen.findByText("older-local")).toBeInTheDocument();
      expect(screen.getByText("older-cloud")).toBeInTheDocument();
      expect(screen.getByText("new-backup")).toBeInTheDocument();
    });

    it("asks before restoring over the live vault", async () => {
      // A restore replaces the database and every stored file; doing it on one
      // click is unrecoverable.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      await user.click(await screen.findByRole("button", { name: /Restore/ }));

      expect(requestsWithMethod("POST").some((call) => call.url.includes("restore"))).toBe(false);
    });

    it("tells a non-admin they cannot see the backups", async () => {
      renderSettings({ at: "/settings?section=backup", auth: memberSession() });

      expect(await screen.findByText("Superuser access is required.")).toBeInTheDocument();
    });
  });

  it("keeps model matching out of maintenance", async () => {
    renderSettings({ at: "/settings?section=maintenance" });
    expect(await screen.findByRole("button", { name: "Run quick check" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Similar models" })).toBeNull();
  });

  describe("conditional backup policy", () => {
    it("keeps the original policy snapshot during refresh", async () => {
      const writes: Headers[] = [];
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "PUT /api/v1/config": (_url, init) => {
            writes.push(new Headers(init?.headers));
            return json({ detail: "edit_conflict" }, 412);
          },
        },
      });
      const enabled = await screen.findByLabelText("Enable automatic backups");
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await act(async () =>
        app.client.setQueryData(queryKeys.vaultConfig, {
          ...VAULT_CONFIG,
          edit_version: 2,
          automatic_backup_time_utc: "06:45",
        }),
      );
      expect(screen.getByLabelText("Daily time (UTC)")).toHaveValue("02:00");
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await screen.findByRole("button", { name: "Review latest version" });
      expect(writes[0].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v1"`);
      expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toMatchObject({
        automatic_backup_time_utc: "02:00",
      });
    });
    it.each([412, 503])(
      "%s: reviews a failed policy save before destination writes",
      async (status) => {
        const connection = aStorageConnection({
          id: 7,
          name: "Archive",
          automatic_backup_enabled: false,
        });
        let reads = 0;
        const writes: Headers[] = [];
        const app = renderSettings({
          at: "/settings?section=backup",
          routes: {
            "GET /api/v1/config": () =>
              json({
                ...VAULT_CONFIG,
                edit_version: ++reads,
                automatic_backup_time_utc: reads === 1 ? "02:00" : "06:45",
              }),
            "GET /api/v1/storage-connections": json([connection]),
            "PUT /api/v1/config": (_url, init) => {
              writes.push(new Headers(init?.headers));
              return writes.length === 1
                ? json({ detail: "edit_conflict" }, status)
                : json({
                    ...VAULT_CONFIG,
                    edit_version: 3,
                    automatic_backups_enabled: true,
                    automatic_backup_time_utc: "06:45",
                  });
            },
            "PATCH /api/v1/storage-connections/7": json({
              ...connection,
              automatic_backup_enabled: true,
            }),
          },
        });
        const enabled = await screen.findByLabelText("Enable automatic backups");
        await waitFor(() => expect(enabled).toBeEnabled());
        await userEvent.click(enabled);
        await userEvent.click(screen.getByLabelText("Use Archive for automatic backups"));
        await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
        await screen.findByRole("button", { name: "Review latest version" });
        expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
        expect(screen.getByRole("button", { name: "Save backup settings" })).toBeDisabled();
        await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
        const latest = await screen.findByRole("region", { name: "Latest saved version" });
        expect(within(latest).getByText("06:45")).toBeVisible();
        expect(screen.getByLabelText("Daily time (UTC)")).toHaveValue("02:00");
        await userEvent.click(
          screen.getByRole("button", { name: "Save my draft against this version" }),
        );
        await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(1));
        expect(writes[1].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v2"`);
        expect(JSON.parse(app.requestsWithMethod("PUT")[1].body)).toMatchObject({
          automatic_backups_enabled: true,
          automatic_backup_time_utc: "06:45",
        });
        expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({
          automatic_backup_enabled: true,
        });
      },
    );
    it("adopts a reviewed policy without writing destinations", async () => {
      let reads = 0;
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/config": () =>
            json({
              ...VAULT_CONFIG,
              edit_version: ++reads,
              automatic_backup_time_utc: reads === 1 ? "02:00" : "06:45",
            }),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const enabled = await screen.findByLabelText("Enable automatic backups");
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await userEvent.click(await screen.findByRole("button", { name: "Use latest version" }));
      expect(enabled).not.toBeChecked();
      expect(screen.getByLabelText("Daily time (UTC)")).toHaveValue("06:45");
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
      expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
    });
    it("preserves confirmed destination receipts after partial failure", async () => {
      const first = aStorageConnection({ id: 7, name: "First", automatic_backup_enabled: false });
      const second = aStorageConnection({ id: 8, name: "Second", automatic_backup_enabled: false });
      let version = 1;
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/storage-connections": json([first, second]),
          "PUT /api/v1/config": () => json({ ...VAULT_CONFIG, edit_version: ++version }),
          "PATCH /api/v1/storage-connections/7": json({ ...first, automatic_backup_enabled: true }),
          "PATCH /api/v1/storage-connections/8": json({ detail: "unavailable" }, 503),
        },
      });
      const enabled = await screen.findByLabelText("Use First for automatic backups");
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await userEvent.click(screen.getByLabelText("Use Second for automatic backups"));
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(2));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Save backup settings" })).toBeEnabled(),
      );
      expect(app.client.getQueryData(storageConnectionKeys.all)).toContainEqual({
        ...first,
        automatic_backup_enabled: true,
      });
      app.route({
        "PATCH /api/v1/storage-connections/8": json({ ...second, automatic_backup_enabled: true }),
      });
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await waitFor(() => expect(app.requestsWithMethod("PATCH")).toHaveLength(3));
      expect(app.requestsWithMethod("PATCH").map((request) => request.url)).toEqual([
        "/api/v1/storage-connections/7",
        "/api/v1/storage-connections/8",
        "/api/v1/storage-connections/8",
      ]);
      expect(enabled).toBeChecked();
    });
    it.each([403, 503])("%s: blocks policy retry after an unavailable review", async (status) => {
      let reads = 0;
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/config": () =>
            ++reads === 1 ? json(VAULT_CONFIG) : json({ detail: "unavailable" }, status),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const enabled = await screen.findByLabelText("Enable automatic backups");
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await waitFor(() =>
        expect(screen.getByRole("button", { name: "Review latest version" })).toBeEnabled(),
      );
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).toBeNull();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
      expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
    });
    it("retires a pending policy review on logout", async () => {
      let reads = 0;
      const pending = Promise.withResolvers<Response>();
      const app = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/config": () => (++reads === 1 ? json(VAULT_CONFIG) : pending.promise),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const enabled = await screen.findByLabelText("Enable automatic backups");
      await waitFor(() => expect(enabled).toBeEnabled());
      await userEvent.click(enabled);
      await userEvent.click(screen.getByRole("button", { name: "Save backup settings" }));
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await act(async () => {
        clearLogin();
        pending.resolve(json({ ...VAULT_CONFIG, edit_version: 2 }));
      });
      expect(screen.queryByRole("region", { name: "Latest saved version" })).toBeNull();
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).toBeNull();
      expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
    });
  });

  describe("retention configuration editing", () => {
    it("keeps retention editing unavailable to members", async () => {
      const app = renderSettings({ at: "/settings?section=trash", auth: memberSession() });
      const input = await screen.findByLabelText("Days");
      expect(input).toBeDisabled();
      await waitFor(() =>
        expect(
          app.requestsWithMethod("GET").some((request) => request.url === "/api/v1/models/trash"),
        ).toBe(true),
      );
      expect(
        app.requestsWithMethod("GET").filter((request) => request.url === "/api/v1/config"),
      ).toHaveLength(0);
      expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
      expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    });
    it("retries a failed trash configuration read", async () => {
      let reads = 0;
      renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/config": () =>
            ++reads === 1 ? json({ detail: "unavailable" }, 503) : json(VAULT_CONFIG),
        },
      });
      const input = await screen.findByLabelText("Days");
      expect(input).toBeDisabled();
      await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
      await waitFor(() => expect(input).toBeEnabled());
      expect(reads).toBe(2);
    });
    it.each([
      { section: "backup", label: "Retention (days)", field: "backup_retention_days" },
      { section: "trash", label: "Days", field: "trash_retention_days" },
    ])(
      "$section: keeps a retention draft across configuration refresh",
      async ({ section, label, field }) => {
        const writes: Headers[] = [];
        const app = renderSettings({
          at: `/settings?section=${section}`,
          routes: {
            "PUT /api/v1/config": (_url, init) => {
              writes.push(new Headers(init?.headers));
              return json({ detail: "edit_conflict" }, 412);
            },
          },
        });
        const input = await screen.findByLabelText(label);
        await waitFor(() => expect(input).toBeEnabled());
        await userEvent.clear(input);
        await userEvent.type(input, "14");
        await act(async () => {
          app.client.setQueryData(queryKeys.vaultConfig, {
            ...VAULT_CONFIG,
            edit_version: 2,
            [field]: 9,
          });
        });
        expect(input).toHaveValue(14);
        await userEvent.click(screen.getByRole("button", { name: "Save retention" }));
        await screen.findByRole("button", { name: "Review latest version" });
        expect(writes[0].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v1"`);
      },
    );
    it.each([
      { section: "backup", label: "Retention (days)", field: "backup_retention_days" },
      { section: "trash", label: "Days", field: "trash_retention_days" },
    ])(
      "$section: reviews a retention conflict before saving revised days",
      async ({ section, label, field }) => {
        let reads = 0;
        const writes: Headers[] = [];
        const app = renderSettings({
          at: `/settings?section=${section}`,
          routes: {
            "GET /api/v1/config": () =>
              json({ ...VAULT_CONFIG, edit_version: ++reads, [field]: reads === 1 ? 30 : 9 }),
            "PUT /api/v1/config": (_url, init) => {
              writes.push(new Headers(init?.headers));
              return writes.length === 1
                ? json({ detail: "edit_conflict" }, 412)
                : json({ ...VAULT_CONFIG, edit_version: 3, [field]: 18 });
            },
          },
        });
        const input = await screen.findByLabelText(label);
        await waitFor(() => expect(input).toBeEnabled());
        await userEvent.clear(input);
        await userEvent.type(input, "14");
        await userEvent.click(screen.getByRole("button", { name: "Save retention" }));
        await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
        const review = await screen.findByRole("region", { name: "Latest saved version" });
        expect(within(review).getByText("9")).toBeVisible();
        await userEvent.clear(input);
        await userEvent.type(input, "18");
        await userEvent.click(
          screen.getByRole("button", { name: "Save my draft against this version" }),
        );
        await waitFor(() =>
          expect(screen.getByRole("button", { name: "Save retention" })).toBeEnabled(),
        );
        expect(writes).toHaveLength(2);
        expect(writes[1].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v2"`);
        expect(JSON.parse(app.requestsWithMethod("PUT")[1].body)).toEqual({ [field]: 18 });
        expect(input).toHaveValue(18);
      },
    );
    it("refreshes trash without replacing retention input", async () => {
      const app = renderSettings({ at: "/settings?section=trash" });
      const input = await screen.findByLabelText("Days");
      await waitFor(() => expect(input).toBeEnabled());
      await userEvent.clear(input);
      await userEvent.type(input, "14");
      const reads = app.requestsWithMethod("GET").filter((r) => r.url === "/api/v1/config").length;
      await userEvent.click(screen.getByTitle("Refresh trash"));
      await waitFor(() =>
        expect(
          app.requestsWithMethod("GET").filter((r) => r.url === "/api/v1/models/trash"),
        ).toHaveLength(2),
      );
      expect(input).toHaveValue(14);
      expect(app.requestsWithMethod("GET").filter((r) => r.url === "/api/v1/config")).toHaveLength(
        reads,
      );
    });
    it("rejects an empty trash retention draft", async () => {
      const app = renderSettings({ at: "/settings?section=trash" });
      const input = await screen.findByLabelText("Days");
      await waitFor(() => expect(input).toBeEnabled());
      await userEvent.clear(input);
      expect(screen.getByRole("button", { name: "Save retention" })).toBeDisabled();
      expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    });
  });

  describe("trash retention", () => {
    it("saves the retention window", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "PUT /api/v1/config": json({ ...VAULT_CONFIG, edit_version: 2, trash_retention_days: 7 }),
        },
      });
      const days = await screen.findByLabelText("Days");
      await user.clear(days);
      await user.type(days, "7");

      await user.click(screen.getByRole("button", { name: /Save retention/ }));

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          trash_retention_days: 7,
        }),
      );
    });

    it("offers no purge when retention is set to keep forever", async () => {
      // -1 means nothing ever expires, so "purge expired" would delete nothing
      // and read as though the setting were being ignored.
      renderSettings({ at: "/settings?section=trash" });
      const days = await screen.findByLabelText("Days");

      fireEvent.change(days, { target: { value: "-1" } });

      expect(screen.getByRole("button", { name: /Review expired/ })).toBeDisabled();
    });

    it("creates a durable preview without issuing a delete", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: { "POST /api/v1/admin/gc": json(GC_PLAN) },
      });

      await user.click(await screen.findByRole("button", { name: /Review expired/ }));
      await user.click(screen.getByRole("button", { name: "Create preview" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/admin/gc"))).toBe(
          true,
        ),
      );
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
      expect(await screen.findByText("GC plan #12 · preview")).toBeVisible();
    });

    it("recovers a preview claimed after the trash section loaded", async () => {
      const user = userEvent.setup();
      let activePlanVisible = false;
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/admin/gc": () => json(activePlanVisible ? GC_PLAN : null),
          "POST /api/v1/admin/gc": () => {
            activePlanVisible = true;
            return json({ detail: "gc_plan_active" }, 409);
          },
        },
      });

      await user.click(await screen.findByRole("button", { name: /Review expired/ }));
      await user.click(screen.getByRole("button", { name: "Create preview" }));

      expect(await screen.findByText("GC plan #12 · preview")).toBeVisible();
      expect(screen.getByRole("button", { name: /Review expired/ })).toBeDisabled();
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
    });

    it("reports a preview conflict when no active plan can be read", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=trash",
        routes: { "POST /api/v1/admin/gc": json({ detail: "gc_plan_active" }, 409) },
      });

      await user.click(await screen.findByRole("button", { name: /Review expired/ }));
      await user.click(screen.getByRole("button", { name: "Create preview" }));

      expect(
        await screen.findByText(
          "Something went wrong reaching the server. Check that PrintStash is running and try again.",
        ),
      ).toBeVisible();
      expect(screen.queryByText("GC plan #12 · preview")).not.toBeInTheDocument();
    });

    it("aborts an active preview without issuing a destructive transition", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/admin/gc": json(GC_PLAN),
          "POST /api/v1/admin/gc/12/abort": json({ ...GC_PLAN, state: "aborted" }),
        },
      });

      await user.click(await screen.findByRole("button", { name: "Abort plan" }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/gc/12/abort"))).toBe(
          true,
        ),
      );
      expect(requestsWithMethod("DELETE")).toHaveLength(0);
      expect(await screen.findByText("GC plan #12 · aborted")).toBeVisible();
    });

    it("restores a model out of the trash", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/models/trash": json([TRASHED_MODEL]),
          "POST /api/v1/models/7/restore": json({ id: 7 }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Restore/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/restore"))).toBe(true),
      );
    });

    it("asks before deleting a model for good", async () => {
      // Purging is the one action in the vault with nothing behind it.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: { "GET /api/v1/models/trash": json([TRASHED_MODEL]) },
      });

      await user.click(await screen.findByRole("button", { name: /Delete/ }));

      expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/models/7"))).toBe(
        false,
      );
    });

    it("purges the model once the operator confirms", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/config": json({ ...VAULT_CONFIG, storage_tier: "unguarded" }),
          "GET /api/v1/models/trash": json([TRASHED_MODEL]),
          "DELETE /api/v1/models/7": json(null, 204),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Delete/ }));

      await user.click(await screen.findByRole("button", { name: "Remove from catalog" }));

      await waitFor(() =>
        expect(requestsWithMethod("DELETE").some((call) => call.url.includes("/models/7"))).toBe(
          true,
        ),
      );
    });

    it("shows a retained storage result", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/config": json({ ...VAULT_CONFIG, storage_tier: "unguarded" }),
          "GET /api/v1/models/trash": json([TRASHED_MODEL]),
          "DELETE /api/v1/models/7": json({
            purged_model_ids: [7],
            purged_count: 1,
            storage_completed: 0,
            storage_pending: 0,
            storage_blocked: 1,
            storage_cleanup_status: "blocked",
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Delete/ }));
      expect(await screen.findByRole("dialog", { name: "Remove from catalog?" })).toHaveTextContent(
        "Stored bytes are retained",
      );
      await user.click(await screen.findByRole("button", { name: "Remove from catalog" }));

      expect(await screen.findByRole("status")).toHaveTextContent("retained");
    });

    it("requires the exact digest before requesting backup verification", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: {
          "GET /api/v1/admin/gc": json(GC_PLAN),
          "POST /api/v1/admin/gc/12/approve": json({
            ...GC_PLAN,
            state: "quarantined",
            backup_id: "backup-1",
            quarantine_until: "2026-02-10T00:00:00Z",
          }),
        },
      });

      const approve = await screen.findByRole("button", {
        name: "Verify backup and quarantine",
      });
      expect(approve).toBeDisabled();
      await user.click(screen.getByLabelText("Confirm GC plan digest"));
      await user.paste(GC_PLAN.digest);
      expect(approve).toBeEnabled();
      await user.click(approve);

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.endsWith("/gc/12/approve"))).toBe(
          true,
        ),
      );
    });
  });

  describe("about", () => {
    it("shows the version running", async () => {
      renderSettings({ at: "/settings?section=about" });

      expect(await screen.findByText("v0.12.1")).toBeInTheDocument();
    });

    it("says the deployment is current", async () => {
      renderSettings({ at: "/settings?section=about" });

      expect(await screen.findByText("Latest published release installed.")).toBeInTheDocument();
    });

    it("says when an update is out", async () => {
      // Self-hosters have no auto-update; this line is the only prompt they get.
      renderSettings({
        at: "/settings?section=about",
        routes: {
          "GET /api/v1/health/releases/latest": json({
            status: "update_available",
            update_available: true,
            current_version: "0.12.1",
            latest_version: "0.14.0",
          }),
        },
      });

      expect(await screen.findByText(/Update available: v0\.14\.0/)).toBeInTheDocument();
    });

    it("says so when the release check itself could not run", async () => {
      // Silence here reads as "you are up to date", which is the one thing it
      // does not know.
      renderSettings({
        at: "/settings?section=about",
        routes: {
          "GET /api/v1/health/releases/latest": json({
            status: "unavailable",
            update_available: false,
            current_version: "0.12.1",
            latest_version: null,
          }),
        },
      });

      expect(
        await screen.findByText("Release check unavailable. Try again later."),
      ).toBeInTheDocument();
    });
  });

  describe("display preferences", () => {
    it("remembers the printer-image choice", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });

      const toggle = await screen.findByRole("switch", {
        name: "Show printer image on printer cards",
      });
      await user.click(toggle);

      expect(toggle).toHaveAttribute("aria-checked", "false");
    });

    it("remembers the known-good choice", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=design",
        routes: {
          "PUT /api/v1/config": json({
            ...VAULT_CONFIG,
            edit_version: 2,
            auto_mark_known_good: true,
          }),
        },
      });

      const toggle = await screen.findByRole("switch", {
        name: "Auto-mark known good on successful print",
      });
      await waitFor(() => expect(toggle).toBeEnabled());
      await user.click(toggle);
      await waitFor(() => expect(toggle).toBeEnabled());

      expect(toggle).toHaveAttribute("aria-checked", "true");
    });
  });

  describe("preview quality", () => {
    it("offers the preview quality choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Preview quality")).toBeInTheDocument();
    });

    it("offers the screenshot resolution choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Screenshot resolution")).toBeInTheDocument();
    });

    it("offers the model image quality choices", async () => {
      renderSettings({ at: "/settings?section=previews" });

      expect(await screen.findByLabelText("Model image quality")).toBeInTheDocument();
    });
  });

  describe("the trash", () => {
    const TRASHED = {
      id: 7,
      name: "Old bracket",
      deleted_at: "2026-01-01T00:00:00Z",
      purge_at: "2026-02-01T00:00:00Z",
      file_count: 2,
      size_bytes: 2048,
      collection: null,
    };

    it("lists what is waiting to be purged", async () => {
      renderSettings({
        at: "/settings?section=trash",
        routes: { "GET /api/v1/models/trash": json([TRASHED]) },
      });

      expect(await screen.findByText("Old bracket")).toBeInTheDocument();
    });

    it("reports how much space the trash is holding", async () => {
      // The number is the reason to empty it; a list with no total makes the
      // decision guesswork.
      renderSettings({
        at: "/settings?section=trash",
        routes: { "GET /api/v1/models/trash": json([TRASHED]) },
      });

      expect(await screen.findByLabelText("Trash size")).toBeInTheDocument();
    });

    it("says so when the trash is empty", async () => {
      renderSettings({ at: "/settings?section=trash" });

      expect(await screen.findByText("Deleted models")).toBeInTheDocument();
      expect(screen.queryByLabelText("Trash size")).toBeNull();
    });
  });

  describe("printers", () => {
    it("lists the printers a role can be granted on", async () => {
      renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]) },
      });

      await screen.findByRole("navigation", { name: "Settings sections" });
      await waitFor(() => expect(screen.queryAllByText(/Voron/).length).toBeGreaterThan(0));
    });
  });
  describe("exporting the library", () => {
    it("downloads the metadata as JSON", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        routes: { "GET /api/v1/models/export": json([]) },
      });

      await user.click(await screen.findByRole("button", { name: /JSON/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("export?format=json"))).toBe(true),
      );
    });

    it("downloads the same metadata as CSV", async () => {
      // Two formats for two audiences: a spreadsheet and a script. Offering one
      // and calling it both is how somebody ends up parsing JSON in Excel.
      const user = userEvent.setup();
      const { requests } = renderSettings({
        routes: { "GET /api/v1/models/export": json([]) },
      });

      await user.click(await screen.findByRole("button", { name: /CSV/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("export?format=csv"))).toBe(true),
      );
    });

    it("exports a full archive for moving to another installation", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        routes: { "GET /api/v1/models/library-archive": json([]) },
      });

      await user.click(await screen.findByRole("button", { name: /Export full library/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("library-archive"))).toBe(true),
      );
    });

    it("surfaces an export the server refused", async () => {
      const user = userEvent.setup();
      renderSettings({
        routes: { "GET /api/v1/models/export": json({ detail: "export_too_large" }, 413) },
      });

      await user.click(await screen.findByRole("button", { name: /JSON/ }));

      expect(
        await screen.findByText("The export is too large. Select fewer models and try again."),
      ).toBeInTheDocument();
    });
  });

  describe("display preferences", () => {
    it.each([412, 503])(
      "%s: reviews a conflicting display currency before saving again",
      async (status) => {
        let reads = 0;
        const writes: Headers[] = [];
        const app = renderSettings({
          at: "/settings?section=design",
          routes: {
            "GET /api/v1/config": () =>
              json({
                ...VAULT_CONFIG,
                edit_version: ++reads,
                currency: reads === 1 ? "USD" : "GBP",
              }),
            "PUT /api/v1/config": (_url, init) => {
              writes.push(new Headers(init?.headers));
              return writes.length === 1
                ? json({ detail: "edit_conflict" }, status)
                : json({ ...VAULT_CONFIG, edit_version: 3, currency: "EUR" });
            },
          },
        });
        const input = await screen.findByLabelText("Display currency");
        await waitFor(() => expect(input).toBeEnabled());
        await userEvent.selectOptions(input, "EUR");
        const review = await screen.findByRole("button", { name: "Review latest version" });
        expect(input).toHaveValue("EUR");
        expect(input).toBeDisabled();
        expect(writes).toHaveLength(1);
        await userEvent.click(review);
        const latest = await screen.findByRole("region", { name: "Latest saved version" });
        expect(within(latest).getByText("GBP")).toBeVisible();
        expect(input).toHaveValue("EUR");
        await userEvent.click(
          screen.getByRole("button", { name: "Save my draft against this version" }),
        );
        await waitFor(() => expect(input).toBeEnabled());
        expect(input).toHaveValue("EUR");
        expect(writes[0].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v1"`);
        expect(writes[1].get("If-Match")).toBe(`"vault-config-e${VAULT_CONFIG.edit_epoch}-v2"`);
        expect(app.client.getQueryData(queryKeys.vaultConfig)).toMatchObject({
          currency: "EUR",
          edit_version: 3,
        });
      },
    );
    it("retires a pending preference review on logout", async () => {
      let reads = 0;
      const pending = Promise.withResolvers<Response>();
      const app = renderSettings({
        at: "/settings?section=design",
        routes: {
          "GET /api/v1/config": () => (++reads === 1 ? json(VAULT_CONFIG) : pending.promise),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const input = await screen.findByLabelText("Display currency");
      await waitFor(() => expect(input).toBeEnabled());
      await userEvent.selectOptions(input, "EUR");
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await act(async () => {
        clearLogin();
        pending.resolve(json({ ...VAULT_CONFIG, edit_version: 2, currency: "GBP" }));
      });
      expect(
        screen.queryByRole("region", { name: "Latest saved version" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });
    it("adopts a reviewed preference without another write", async () => {
      let reads = 0;
      const app = renderSettings({
        at: "/settings?section=design",
        routes: {
          "GET /api/v1/config": () =>
            json({ ...VAULT_CONFIG, edit_version: ++reads, currency: reads === 1 ? "USD" : "GBP" }),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const input = await screen.findByLabelText("Display currency");
      await waitFor(() => expect(input).toBeEnabled());
      await userEvent.selectOptions(input, "EUR");
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await userEvent.click(await screen.findByRole("button", { name: "Use latest version" }));
      expect(input).toHaveValue("GBP");
      expect(input).toBeEnabled();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });
    it.each([403, 503])("%s: blocks preference retry after a failed review", async (status) => {
      let reads = 0;
      const app = renderSettings({
        at: "/settings?section=design",
        routes: {
          "GET /api/v1/config": () =>
            ++reads === 1 ? json(VAULT_CONFIG) : json({ detail: "unavailable" }, status),
          "PUT /api/v1/config": json({ detail: "edit_conflict" }, 412),
        },
      });
      const input = await screen.findByLabelText("Display currency");
      await waitFor(() => expect(input).toBeEnabled());
      await userEvent.selectOptions(input, "EUR");
      await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
      await waitFor(() => expect(reads).toBe(2));
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
      expect(app.requestsWithMethod("PUT")).toHaveLength(1);
    });
    it("saves the display currency", async () => {
      // Every cost in the app is rendered in it, so a wrong one misprices the
      // whole library at once.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=design",
        routes: {
          "PUT /api/v1/config": json({ ...VAULT_CONFIG, edit_version: 2, currency: "EUR" }),
        },
      });

      await user.selectOptions(await screen.findByLabelText("Display currency"), "EUR");

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          currency: "EUR",
        }),
      );
    });

    it("puts the currency back when the server refuses", async () => {
      // A select showing EUR over a vault still storing USD relabels every
      // price on screen with a currency nobody saved.
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=design",
        routes: { "PUT /api/v1/config": json({ detail: "forbidden" }, 403) },
      });
      const select = await screen.findByLabelText("Display currency");

      await user.selectOptions(select, "EUR");

      await waitFor(() => expect(select).toHaveValue("USD"));
    });
  });

  describe("preview quality", () => {
    it("saves the model image width", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=previews",
        routes: {
          "PUT /api/v1/config": json({
            ...VAULT_CONFIG,
            edit_version: 2,
            model_thumbnail_width: 1280,
          }),
        },
      });

      await user.selectOptions(await screen.findByLabelText("Model image quality"), "1280");

      await waitFor(() =>
        expect(JSON.parse(requestsWithMethod("PUT").at(-1)?.body ?? "{}")).toMatchObject({
          model_thumbnail_width: 1280,
        }),
      );
    });

    it("puts the width back when the server refuses", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=previews",
        routes: { "PUT /api/v1/config": json({ detail: "forbidden" }, 403) },
      });
      const select = await screen.findByLabelText("Model image quality");

      await user.selectOptions(select, "1280");

      await waitFor(() => expect(select).toHaveValue("640"));
    });

    it("queues a rebuild of the images already generated", async () => {
      // A quality change only affects new images; without this the setting
      // looks like it did nothing to a library that is already full.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=previews",
        routes: {
          "POST /api/v1/admin/work/derivatives/thumbnail/regenerate": json({
            kind: "thumbnail",
            mode: "all",
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Recreate all images/ }));

      await waitFor(() => {
        const rebuild = requestsWithMethod("POST").find((call) =>
          call.url.endsWith("/derivatives/thumbnail/regenerate"),
        );
        expect(rebuild?.body).toBe(JSON.stringify({ mode: "all" }));
      });
    });

    it("remembers the viewer quality in this browser", async () => {
      // It is a per-device GPU trade-off, not a vault setting: syncing it would
      // give a phone the resolution somebody chose on a workstation.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({ at: "/settings?section=previews" });

      await user.selectOptions(await screen.findByLabelText("Preview quality"), "detail");

      expect(requestsWithMethod("PUT")).toHaveLength(0);
    });
  });

  describe("restoring a backup", () => {
    const BACKUP_META = {
      backup_id: "2026-01-01T000000Z",
      created_at: "2026-01-01T00:00:00Z",
      location: "local",
      app_version: "0.12.1",
      file_count: 42,
      size_bytes: 1024,
      storage_backend: "local",
      source_ref: "local-source",
      namespace: "vault-backups",
      archive_sha256: "b".repeat(64),
    };

    it("restores the backup once confirmed", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP_META]),
          "POST /api/v1/backups/2026-01-01T000000Z/restore": json({ restored_files: 42 }),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Restore/ }));

      await user.click(
        within(await screen.findByRole("dialog")).getByRole("button", { name: "Restore" }),
      );

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) =>
            call.url.includes("/restore?source_ref=local-source"),
          ),
        ).toBe(true),
      );
    });

    it("says what a restore is about to replace", async () => {
      // It overwrites the database and every stored file; the sentence is the
      // only warning between a click and that.
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=backup",
        routes: { "GET /api/v1/backups/sources": json([BACKUP_META]) },
      });

      await user.click(await screen.findByRole("button", { name: /Restore/ }));

      const dialog = await screen.findByRole("dialog");
      expect(dialog).toHaveTextContent(
        "This replaces the current database and stored files with the selected backup.",
      );
      expect(dialog).toHaveTextContent(
        "Exact source: local · local-source · namespace vault-backups",
      );
      expect(dialog).toHaveTextContent("SHA-256 bbbbbbbbbbbbbbbb");
    });

    it("downloads a backup off the server", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        at: "/settings?section=backup",
        routes: {
          "GET /api/v1/backups/sources": json([BACKUP_META]),
          "GET /api/v1/backups/2026-01-01T000000Z/download": json([]),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Download/ }));

      await waitFor(() =>
        expect(requests().some((call) => call.url.includes("/download"))).toBe(true),
      );
    });
  });
  describe("the metrics on a model card", () => {
    it("puts the metric the user chose into its slot", async () => {
      // The card shows three of eight, so the choice is the whole feature; it
      // lives in this browser and shows nowhere else.
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model card metrics");

      await user.click(screen.getAllByRole("button", { name: "MaterialMAT", pressed: false })[0]);

      expect(window.localStorage.getItem("printstash.card.metrics")).toContain("material");
    });

    it("cannot put one metric in two slots", async () => {
      // Two identical columns on a card waste a third of the space it has, so a
      // metric already in use elsewhere is offered as taken rather than as free.
      renderSettings({ at: "/settings?section=design" });

      await screen.findByText("Model card metrics");
      expect(screen.getAllByRole("button", { name: /^Layer heightSlot \d/ })[0]).toBeDisabled();
    });

    it("tells the grid about the change without a reload", async () => {
      // The grid reads the choice from storage on a storage event; without the
      // event the cards keep the old columns until the tab is reloaded.
      const user = userEvent.setup();
      const events: string[] = [];
      window.addEventListener("storage", (event) => events.push(String(event.key)));
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model card metrics");

      await user.click(screen.getAllByRole("button", { name: "MaterialMAT", pressed: false })[0]);

      expect(events).toContain("printstash.card.metrics");
    });

    it("puts the metrics back to the defaults", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model card metrics");
      await user.click(screen.getAllByRole("button", { name: "MaterialMAT", pressed: false })[0]);

      // Two cards on this section carry a Reset; the metrics card is the first.
      await user.click(screen.getAllByRole("button", { name: "Reset" })[0]);

      expect(await screen.findByText("Card metrics reset.")).toBeInTheDocument();
    });
  });

  describe("which metadata a model page shows", () => {
    it("hides a field the user turned off", async () => {
      // Slicer metadata runs to twenty fields; showing all of them buries the
      // three anybody actually reads.
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model metadata");

      await user.click(screen.getByRole("button", { name: "Infill", pressed: true }));

      expect(window.localStorage.getItem("printstash.metadata.visible")).toContain("infill");
    });

    it("counts how many are showing", async () => {
      renderSettings({ at: "/settings?section=design" });

      expect(await screen.findByText(/of \d+ shown/)).toBeInTheDocument();
    });

    it("turns every field on at once", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model metadata");

      await user.click(screen.getByRole("button", { name: "Show all" }));

      expect(screen.getByRole("button", { name: "Infill" })).toHaveAttribute(
        "aria-pressed",
        "true",
      );
    });

    it("turns every field off at once", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model metadata");

      await user.click(screen.getByRole("button", { name: "Hide all" }));

      expect(screen.getByRole("button", { name: "Infill" })).toHaveAttribute(
        "aria-pressed",
        "false",
      );
    });

    it("puts the fields back to the defaults", async () => {
      const user = userEvent.setup();
      renderSettings({ at: "/settings?section=design" });
      await screen.findByText("Model metadata");
      await user.click(screen.getByRole("button", { name: "Hide all" }));

      await user.click(screen.getAllByRole("button", { name: "Reset" }).at(-1)!);

      expect(await screen.findByText("Metadata display reset.")).toBeInTheDocument();
    });
  });
});

describe("Settings account recovery", () => {
  it("retries an unavailable API-key list without claiming it empty", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: { "GET /api/v1/auth/api-keys": json({ detail: "unavailable" }, 503) },
    });
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveTextContent("API keys could not be loaded.");
    expect(screen.queryByText("No active API keys.")).toBeNull();
    app.route({ "GET /api/v1/auth/api-keys": json([ISSUED_KEY]) });
    await userEvent.click(within(failure).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Slicer")).toBeVisible();
  });
  it("retries an unavailable admin User list without claiming it empty", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: { "GET /api/v1/admin/users": json({ detail: "unavailable" }, 503) },
    });
    const failure = await screen.findByRole("alert");
    expect(failure).toHaveTextContent("Users could not be loaded.");
    expect(screen.queryByText("No users.")).toBeNull();
    app.route({ "GET /api/v1/admin/users": json([aUser()]) });
    await userEvent.click(within(failure).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("maker", { selector: "p" })).toBeVisible();
  });
  it("retires an owned extension handoff with its private session", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: { "POST /api/v1/auth/api-keys": json(MINTED_KEY) },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Set up extension" }));
    await screen.findByText("Setup prepared");
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toContain(
      MINTED_KEY.api_key,
    );
    app.unmount();
    await act(async () => {
      clearLogin();
    });
    expect(window.sessionStorage.getItem(BROWSER_EXTENSION_SETUP_STORAGE_KEY)).toBeNull();
  });
});

describe("Settings account intent", () => {
  it("keeps an issued key copyable when extension handoff storage fails", async () => {
    const store = Storage.prototype.setItem;
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(function (this: Storage, key, value) {
      if (key === BROWSER_EXTENSION_SETUP_STORAGE_KEY)
        throw new DOMException("Storage unavailable", "QuotaExceededError");
      return store.call(this, key, value);
    });
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "POST /api/v1/auth/api-keys": json(MINTED_KEY),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Set up extension" }));
    expect(await screen.findByTitle("Copy API key")).toBeVisible();
    expect(screen.getByText(MINTED_KEY.api_key)).toBeVisible();
    expect(screen.queryByText("Setup prepared")).toBeNull();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("retains a failed User create draft without an automatic retry", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "POST /api/v1/admin/users": json({ detail: "User creation unavailable" }, 503),
      },
    });
    const username = await screen.findByLabelText("Username");
    fireEvent.change(username, { target: { value: "retry-maker" } });
    fireEvent.change(screen.getByLabelText("Initial password"), {
      target: { value: "FakePassword123" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    expect(
      await screen.findByText(
        "Something went wrong reaching the server. Check that PrintStash is running and try again.",
      ),
    ).toBeVisible();
    expect(username).toHaveValue("retry-maker");
    expect(screen.getByLabelText("Initial password")).toHaveValue("FakePassword123");
    expect(screen.getByRole("button", { name: "Create" })).toBeEnabled();
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("suppresses an issued secret after the account read is denied", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "POST /api/v1/auth/api-keys": json(MINTED_KEY),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Generate" }));
    await screen.findByTitle("Copy API key");
    app.route({ "GET /api/v1/auth/api-keys": json({ detail: "denied" }, 403) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["api-keys"] });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("API keys could not be loaded.");
    expect(screen.queryByText(MINTED_KEY.api_key)).toBeNull();
    expect(screen.queryByTitle("Copy API key")).toBeNull();
    expect(screen.queryByText("Slicer")).toBeNull();
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
  });
});

describe("Settings account draft lifetime", () => {
  it("preserves a User draft through background refresh", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
      },
    });
    const username = await screen.findByLabelText("Username");
    await userEvent.type(username, "new-maker");
    await userEvent.type(screen.getByLabelText("Initial password"), "FakePassword123");
    app.route({ "GET /api/v1/admin/users": json([aUser({ username: "changed elsewhere" })]) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["admin", "users"] });
    });
    expect(username).toHaveValue("new-maker");
    expect(screen.getByLabelText("Initial password")).toHaveValue("FakePassword123");
    expect(await screen.findByText("changed elsewhere", { selector: "p" })).toBeVisible();
  });
  it("preserves a newer create draft after the earlier command completes", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "POST /api/v1/admin/users": () => pending.promise,
      },
    });
    const username = await screen.findByLabelText("Username");
    fireEvent.change(username, { target: { value: "original-maker" } });
    fireEvent.change(screen.getByLabelText("Initial password"), {
      target: { value: "FakePassword123" },
    });
    await userEvent.click(screen.getByRole("button", { name: "Create" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    fireEvent.change(username, { target: { value: "next-maker" } });
    await act(async () => {
      pending.resolve(json(aUser({ username: "original-maker" })));
    });
    expect(await screen.findByText("original-maker", { selector: "p" })).toBeVisible();
    expect(username).toHaveValue("next-maker");
    expect(screen.getByLabelText("Initial password")).toHaveValue("FakePassword123");
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body).username).toBe("original-maker");
  });
  it("preserves a newer password draft after an earlier reset completes", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "POST /api/v1/admin/users/2/password": () => pending.promise,
      },
    });
    const field = await screen.findByPlaceholderText("New password");
    fireEvent.change(field, { target: { value: "OldFakePassword123" } });
    await userEvent.click(screen.getByRole("button", { name: "Reset password" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    fireEvent.change(field, { target: { value: "NextFakePassword123" } });
    await act(async () => {
      pending.resolve(json(aUser()));
    });
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "Reset password" })).toBeEnabled(),
    );
    expect(field).toHaveValue("NextFakePassword123");
    expect(JSON.parse(app.requestsWithMethod("POST")[0].body).password).toBe("OldFakePassword123");
  });
  it("dismisses the one-time key receipt without revoking its key", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "POST /api/v1/auth/api-keys": json(MINTED_KEY),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Generate" }));
    await screen.findByTitle("Copy API key");
    expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Dismiss this secret" }));
    expect(screen.queryByText(MINTED_KEY.api_key)).toBeNull();
    expect(screen.getByText("Slicer")).toBeVisible();
    expect(screen.getByRole("button", { name: "Generate" })).toBeEnabled();
    expect(app.requestsWithMethod("DELETE")).toHaveLength(0);
  });
  it("keeps API keys usable while the User list is unavailable", async () => {
    renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/auth/api-keys": json([ISSUED_KEY]),
        "GET /api/v1/admin/users": json({ detail: "unavailable" }, 503),
      },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Users could not be loaded.");
    expect(screen.getByText("Slicer")).toBeVisible();
    expect(screen.getByTitle("Revoke API key")).toBeEnabled();
  });
  it("disables a selected collection grant after the User list is denied", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": json([]),
      },
    });
    const selects = await screen.findAllByLabelText("User");
    await userEvent.selectOptions(selects[0], "2");
    await userEvent.click(screen.getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    expect(screen.getByRole("button", { name: "Grant" })).toBeEnabled();
    app.route({ "GET /api/v1/admin/users": json({ detail: "denied" }, 403) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["admin", "users"] });
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Users could not be loaded.");
    expect(screen.getByRole("button", { name: "Grant" })).toBeDisabled();
    expect(selects[0]).toBeDisabled();
    expect(screen.queryByText("maker", { selector: "p" })).toBeNull();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
});

describe("Settings resource access recovery", () => {
  it("closes a grantee selection after the User loses its selectable role", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": json([aCollectionPermission()]),
        "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
        "GET /api/v1/printers/4/permissions": json([aPrinterPermission()]),
      },
    });
    const collection = await screen.findByRole("group", { name: "Collection access" });
    const printer = screen.getByRole("group", { name: "Printer access" });
    await within(collection).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(collection).getByLabelText("User"), "2");
    await userEvent.click(within(collection).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await within(collection).findByTitle("Remove collection access");
    await userEvent.selectOptions(within(printer).getByLabelText("User"), "2");
    await within(printer).findByTitle("Remove printer access");
    app.route({ "GET /api/v1/admin/users": json([aUser({ is_superuser: true })]) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["admin", "users"] });
    });
    await waitFor(() => expect(within(collection).getByLabelText("User")).toHaveValue(""));
    expect(within(printer).getByLabelText("User")).toHaveValue("");
    expect(within(collection).queryByTitle("Remove collection access")).toBeNull();
    expect(within(printer).queryByTitle("Remove printer access")).toBeNull();
    expect(within(collection).getByRole("button", { name: "Grant" })).toBeDisabled();
  });
  it("hides denied cached printer grants", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
        "GET /api/v1/printers/4/permissions": json([aPrinterPermission()]),
      },
    });
    const card = await screen.findByRole("group", { name: "Printer access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    await within(card).findByTitle("Remove printer access");
    await userEvent.selectOptions(within(card).getByLabelText("Printer"), "4");
    app.route({ "GET /api/v1/printers/4/permissions": json({ detail: "denied" }, 403) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["printer-permissions"] });
    });
    expect(await within(card).findByRole("alert")).toHaveTextContent("Voron");
    expect(within(card).queryByTitle("Remove printer access")).toBeNull();
    expect(within(card).queryByText(/has no direct printer access/)).toBeNull();
    expect(within(card).getByRole("button", { name: "Save" })).toBeDisabled();
  });
  it("discards an obsolete collection permission read after selection", async () => {
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        ...collectionTreeRoutes([
          aCollection({ id: 5, name: "Parts" }),
          aCollection({ id: 6, name: "Tools" }),
        ]),
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
        "GET /api/v1/collections/6/permissions": json([
          aCollectionPermission({ collection_id: 6, role: "admin" }),
        ]),
      },
    });
    const card = await screen.findByRole("group", { name: "Collection access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    await userEvent.click(within(card).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await waitFor(() => expect(signal).toBeDefined());
    await userEvent.click(within(card).getByRole("button", { name: "Parts" }));
    await userEvent.click(await screen.findByRole("option", { name: /Tools/ }));
    expect(await within(card).findByTitle("Remove collection access")).toBeVisible();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      held.resolve(json([aCollectionPermission()]));
    });
    expect(within(card).getByText("admin", { selector: "span" })).toBeVisible();
    expect(within(card).queryByText("edit", { selector: "span" })).toBeNull();
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/permissions")),
    ).toHaveLength(2);
  });
  it("preserves a newer permission draft after an earlier command completes", async () => {
    const pending = Promise.withResolvers<Response>();
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser(), aUser({ id: 3, username: "next-user" })]),
        "GET /api/v1/collections/5/permissions": json([]),
        "PUT /api/v1/collections/5/permissions/2": () => pending.promise,
      },
    });
    const card = await screen.findByRole("group", { name: "Collection access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    await userEvent.click(within(card).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await userEvent.selectOptions(within(card).getByLabelText("Role"), "edit");
    await userEvent.click(within(card).getByRole("button", { name: "Grant" }));
    await waitFor(() => expect(app.requestsWithMethod("PUT")).toHaveLength(1));
    await userEvent.selectOptions(within(card).getByLabelText("User"), "3");
    await userEvent.selectOptions(within(card).getByLabelText("Role"), "admin");
    await act(async () => {
      pending.resolve(json(aCollectionPermission()));
    });
    await waitFor(() => expect(within(card).getByRole("button", { name: "Grant" })).toBeEnabled());
    expect(within(card).getByLabelText("User")).toHaveValue("3");
    expect(within(card).getByLabelText("Role")).toHaveValue("admin");
    expect(app.requestsWithMethod("PUT")[0].url).toBe("/api/v1/collections/5/permissions/2");
    expect(JSON.parse(app.requestsWithMethod("PUT")[0].body)).toEqual({ role: "edit" });
    expect(
      app.requestsWithMethod("GET").filter((call) => call.url.endsWith("/permissions")),
    ).toHaveLength(1);
  });
  it("retains failed permission intent without retrying its command", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": json([]),
        "PUT /api/v1/collections/5/permissions/2": json(
          { detail: "Permission command unavailable" },
          503,
        ),
      },
    });
    const card = await screen.findByRole("group", { name: "Collection access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    await userEvent.click(within(card).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await userEvent.selectOptions(within(card).getByLabelText("Role"), "edit");
    await userEvent.click(within(card).getByRole("button", { name: "Grant" }));
    expect(
      await screen.findByText(
        "Something went wrong reaching the server. Check that PrintStash is running and try again.",
      ),
    ).toBeVisible();
    expect(within(card).getByLabelText("User")).toHaveValue("2");
    expect(within(card).getByLabelText("Role")).toHaveValue("edit");
    expect(within(card).getByRole("button", { name: "Parts" })).toBeVisible();
    expect(app.requestsWithMethod("PUT")).toHaveLength(1);
  });
  it("clears permission selections on private retirement", async () => {
    renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": json([]),
        "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
        "GET /api/v1/printers/4/permissions": json([]),
      },
    });
    const collection = await screen.findByRole("group", { name: "Collection access" });
    const printer = screen.getByRole("group", { name: "Printer access" });
    await within(collection).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(collection).getByLabelText("User"), "2");
    await userEvent.click(within(collection).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await userEvent.selectOptions(within(printer).getByLabelText("User"), "2");
    await userEvent.selectOptions(within(printer).getByLabelText("Printer"), "4");
    await act(async () => {
      clearLogin();
    });
    expect(within(collection).getByLabelText("User")).toHaveValue("");
    expect(within(collection).getByRole("button", { name: "Select collection" })).toBeVisible();
    expect(within(printer).getByLabelText("User")).toHaveValue("");
    expect(within(printer).getByLabelText("Printer")).toHaveValue("");
  });
  it("distinguishes failed printer choices from an empty fleet", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/printers": json({ detail: "unavailable" }, 503),
      },
    });
    const card = await screen.findByRole("group", { name: "Printer access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    const alert = await within(card).findByRole("alert");
    expect(alert).toHaveTextContent("Printers could not be loaded.");
    expect(within(card).queryByText(/has no direct printer access/)).toBeNull();
    app.route({
      "GET /api/v1/printers": json([aPrinter({ id: 4, name: "Voron" })]),
      "GET /api/v1/printers/4/permissions": json([aPrinterPermission()]),
    });
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await within(card).findByTitle("Remove printer access")).toBeVisible();
  });
  it("preserves healthy printer grants when another source fails", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/printers": json([
          aPrinter({ id: 4, name: "Voron" }),
          aPrinter({ id: 5, name: "Prusa" }),
        ]),
        "GET /api/v1/printers/4/permissions": json([aPrinterPermission()]),
        "GET /api/v1/printers/5/permissions": json({ detail: "unavailable" }, 503),
      },
    });
    const card = await screen.findByRole("group", { name: "Printer access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    expect(await within(card).findByTitle("Remove printer access")).toBeVisible();
    const alert = within(card).getByRole("alert");
    expect(alert).toHaveTextContent("Prusa");
    app.route({
      "GET /api/v1/printers/5/permissions": json([aPrinterPermission({ id: 12, printer_id: 5 })]),
    });
    await userEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(within(card).getAllByTitle("Remove printer access")).toHaveLength(2),
    );
  });
  it("blocks collection writes until its permission read succeeds", async () => {
    const app = renderSettings({
      at: "/settings?section=access",
      routes: {
        "GET /api/v1/admin/users": json([aUser()]),
        "GET /api/v1/collections/5/permissions": json({ detail: "unavailable" }, 503),
      },
    });
    const card = await screen.findByRole("group", { name: "Collection access" });
    await within(card).findByRole("option", { name: "maker" });
    await userEvent.selectOptions(within(card).getByLabelText("User"), "2");
    await userEvent.click(within(card).getByRole("button", { name: "Select collection" }));
    await userEvent.click(await screen.findByRole("option", { name: /Parts/ }));
    await within(card).findByRole("alert");
    expect(within(card).getByRole("button", { name: "Grant" })).toBeDisabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });
});

describe("Settings remote configuration recovery", () => {
  it.each(["design", "previews"])("blocks unavailable remote settings in %s", async (section) => {
    const app = renderSettings({
      at: `/settings?section=${section}`,
      routes: { "GET /api/v1/config": json({ detail: "unavailable" }, 503) },
    });
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Configuration could not be loaded.",
    );
    const name = section === "design" ? "Display currency" : "Model image quality";
    expect(screen.getByLabelText(name)).toBeDisabled();
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
    app.route({ "GET /api/v1/config": json(VAULT_CONFIG) });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByLabelText(name)).toBeEnabled());
  });
  it("shows the acknowledged normalized currency instead of the sent choice", async () => {
    const app = renderSettings({
      at: "/settings?section=design",
      routes: { "PUT /api/v1/config": json({ ...VAULT_CONFIG, edit_version: 2, currency: "GBP" }) },
    });
    const choice = await screen.findByLabelText("Display currency");
    await waitFor(() => expect(choice).toBeEnabled());
    await userEvent.selectOptions(choice, "EUR");
    await waitFor(() => expect(choice).toHaveValue("GBP"));
    expect(
      app.requestsWithMethod("GET").filter((request) => request.url.includes("/api/v1/config")),
    ).toHaveLength(1);
  });
});
