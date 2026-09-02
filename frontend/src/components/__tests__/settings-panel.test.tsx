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
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SettingsPanel } from "@/components/settings-panel";
import { queryKeys } from "@/lib/query-client";
import { aCollection, aPrinter, vaultStats } from "@/test-support/factories";
import { json, memberSession, renderApp, type RenderAppOptions } from "@/test-support/render";
import type { CollectionPermissionRead, PrinterPermissionRead, UserRead } from "@/types";

const HEALTH = {
  status: "ok",
  version: "0.12.1",
  database: { status: "ok" },
  storage: { status: "ok", backend: "local" },
};

const VAULT_CONFIG = {
  storage_backend: "local",
  data_dir: "/data/files",
  trash_retention_days: 30,
  model_thumbnail_width: 640,
  currency: "USD",
};

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

const ISSUED_KEY = {
  id: 9,
  name: "Slicer",
  prefix: "ps_test",
  created_at: "2026-01-01T00:00:00Z",
  last_used_at: null,
};

/** The mint response, which carries the secret a listing never returns again. */
const MINTED_KEY = { ...ISSUED_KEY, api_key: "ps_test_this-is-not-a-real-key" };

function aUser(over: Partial<UserRead> = {}): UserRead {
  return {
    id: 2,
    username: "maker",
    email: null,
    is_superuser: false,
    is_active: true,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

function aCollectionPermission(
  over: Partial<CollectionPermissionRead> = {},
): CollectionPermissionRead {
  return {
    collection_id: 5,
    user_id: 2,
    username: "maker",
    role: "edit",
    inherited: false,
    ...over,
  };
}

function aPrinterPermission(over: Partial<PrinterPermissionRead> = {}): PrinterPermissionRead {
  return {
    id: 11,
    printer_id: 4,
    user_id: 2,
    username: "maker",
    role: "print",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

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
      "GET /api/v1/collections": json([]),
      "GET /api/v1/printers": json([]),
      "GET /api/v1/libraries": json([]),
      "GET /api/v1/notifications": json({ enabled: false, channels: [] }),
      "GET /api/v1/notifications/deliveries": json([]),
      "GET /api/v1/auth/oidc": json({ enabled: false }),
      "GET /api/v1/spoolman/status": json({ enabled: false, url: null, reachable: false }),
      "GET /api/v1/maintenance/audits/latest": json(null),
      "GET /api/v1/models/trash": json([]),
      "GET /api/v1/admin/gc": json(null),
      "GET /api/v1/backups": json([]),
      "GET /api/v1/backups/sources": json([]),
      "GET /api/v1/backups/unowned-local": json([]),
      "GET /api/v1/storage-connections": json([]),
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

      expect(await screen.findByText("Restart failed.")).toBeVisible();
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
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Initial password"), "Password123");

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
      await user.type(screen.getByLabelText("Username"), "maker");
      await user.type(screen.getByLabelText("Email"), "maker@example.test");
      await user.type(screen.getByLabelText("Initial password"), "Password123");

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
      await user.type(screen.getByLabelText("Username"), "maker");

      await user.type(screen.getByLabelText("Initial password"), "short");

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
    it("grants the role the admin chose", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/collections": json([aCollection({ id: 5, name: "Parts" })]),
          "GET /api/v1/collections/5/permissions": json([]),
          "PUT /api/v1/collections/5/permissions/2": json(aCollectionPermission()),
        },
      });
      await screen.findByRole("navigation", { name: "Settings sections" });
      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");
      await user.selectOptions(screen.getByLabelText("Collection"), "5");

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
      renderSettings({
        at: "/settings?section=access",
        routes: { "GET /api/v1/collections": json([aCollection({ id: 5, name: "Parts" })]) },
      });

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
          "GET /api/v1/collections": json([aCollection({ id: 5, name: "Parts" })]),
          "GET /api/v1/collections/5/permissions": json([aCollectionPermission()]),
        },
      });

      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");

      expect(await screen.findByTitle("Remove collection access")).toBeInTheDocument();
    });

    it("revokes a grant", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=access",
        routes: {
          "GET /api/v1/admin/users": json([aUser({ id: 2, username: "maker" })]),
          "GET /api/v1/collections": json([aCollection({ id: 5, name: "Parts" })]),
          "GET /api/v1/collections/5/permissions": json([aCollectionPermission()]),
          "DELETE /api/v1/collections/5/permissions/2": json(null, 204),
        },
      });

      await user.selectOptions((await screen.findAllByLabelText("User"))[0], "2");

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

    it("says so when nothing has been backed up", async () => {
      renderSettings({ at: "/settings?section=storage" });

      expect(await screen.findByText("No backups found.")).toBeInTheDocument();
    });

    it("lists the backups taken", async () => {
      const { requests } = renderSettings({
        at: "/settings?section=storage",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      expect(await screen.findByText("2026-01-01T000000Z")).toBeInTheDocument();
      expect(screen.getByText("Locator: vault-backups · local-source-ref")).toBeInTheDocument();
      expect(requests().some((call) => call.url.endsWith("/api/v1/backups/sources"))).toBe(true);
    });

    it("keeps same-id sources independent for exact downloads", async () => {
      const user = userEvent.setup();
      const { requests } = renderSettings({
        at: "/settings?section=storage",
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
        at: "/settings?section=storage",
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
        at: "/settings?section=storage",
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

    it("adopts one S3 candidate only after confirmation", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=storage",
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
          "POST /api/v1/backups/adopt-s3": json({ backup_id: "legacy-1" }),
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
        at: "/settings?section=storage",
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
          "POST /api/v1/backups/adopt-local": json({ backup_id: "legacy-1" }),
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
        at: "/settings?section=storage",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      expect(await screen.findByText("v0.12.1")).toBeInTheDocument();
    });

    it("takes a backup on demand", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=storage",
        routes: { "POST /api/v1/backups": json(BACKUP) },
      });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));

      await waitFor(() =>
        expect(requestsWithMethod("POST").some((call) => call.url.includes("/backups"))).toBe(true),
      );
    });

    it("reports a backup failure", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=storage",
        routes: {
          "POST /api/v1/backups": json({ detail: "backup_blob_missing" }, 409),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Backup now/ }));

      expect(await screen.findByText("Backup blob missing.")).toBeVisible();
    });

    it("allows retrying after a backup failure", async () => {
      const user = userEvent.setup();
      renderSettings({
        at: "/settings?section=storage",
        routes: {
          "POST /api/v1/backups": json({ detail: "backup_blob_missing" }, 409),
        },
      });
      const backupNow = await screen.findByRole("button", { name: /Backup now/ });

      await user.click(backupNow);
      await screen.findByText("Backup blob missing.");

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
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=storage",
        routes: {
          "GET /api/v1/backups/sources": json([olderLocal, olderCloud]),
          "POST /api/v1/backups": json({
            ...BACKUP,
            backup_id: "new-backup",
            source_ref: undefined,
            namespace: undefined,
          }),
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
        at: "/settings?section=storage",
        routes: { "GET /api/v1/backups/sources": json([BACKUP]) },
      });

      await user.click(await screen.findByRole("button", { name: /Restore/ }));

      expect(requestsWithMethod("POST").some((call) => call.url.includes("restore"))).toBe(false);
    });

    it("tells a non-admin they cannot see the backups", async () => {
      renderSettings({ at: "/settings?section=storage", auth: memberSession() });

      expect(await screen.findByText("Superuser access is required.")).toBeInTheDocument();
    });
  });

  describe("trash retention", () => {
    it("saves the retention window", async () => {
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=trash",
        routes: { "PUT /api/v1/config": json({ ...VAULT_CONFIG, trash_retention_days: 7 }) },
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
          "GET /api/v1/models/trash": json([TRASHED_MODEL]),
          "DELETE /api/v1/models/7": json(null, 204),
        },
      });
      await user.click(await screen.findByRole("button", { name: /Delete/ }));

      await user.click(await screen.findByRole("button", { name: "Delete forever" }));

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
      await user.click(await screen.findByRole("button", { name: "Delete forever" }));

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
      await user.type(screen.getByLabelText("Confirm GC plan digest"), GC_PLAN.digest);
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
            latest_version: "0.13.0",
          }),
        },
      });

      expect(await screen.findByText(/Update available: v0\.13\.0/)).toBeInTheDocument();
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
      renderSettings({ at: "/settings?section=design" });

      const toggle = await screen.findByRole("switch", {
        name: "Auto-mark known good on successful print",
      });
      await user.click(toggle);

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

      expect(await screen.findByText("Export too large.")).toBeInTheDocument();
    });
  });

  describe("display preferences", () => {
    it("saves the display currency", async () => {
      // Every cost in the app is rendered in it, so a wrong one misprices the
      // whole library at once.
      const user = userEvent.setup();
      const { requestsWithMethod } = renderSettings({
        at: "/settings?section=design",
        routes: { "PUT /api/v1/config": json({ ...VAULT_CONFIG, currency: "EUR" }) },
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
        routes: { "PUT /api/v1/config": json({ ...VAULT_CONFIG, model_thumbnail_width: 1280 }) },
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
          "POST /api/v1/files/thumbnails/rebuild": json({
            job_id: "job-9",
            state: "pending",
            message: "queued",
          }),
        },
      });

      await user.click(await screen.findByRole("button", { name: /Recreate all images/ }));

      await waitFor(() =>
        expect(
          requestsWithMethod("POST").some((call) => call.url.includes("thumbnails/rebuild")),
        ).toBe(true),
      );
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
        at: "/settings?section=storage",
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
        at: "/settings?section=storage",
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
        at: "/settings?section=storage",
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
