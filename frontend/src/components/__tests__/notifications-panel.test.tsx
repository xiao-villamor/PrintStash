/*
 * Telling somebody a print finished — or that a printer went quiet.
 *
 * A channel is a credential the operator hands over once: a webhook URL, a bot
 * token, an ntfy access token. The panel reads those back masked, and that
 * creates the one bug this file exists to prevent — an edit that saves the mask
 * as the value, silently replacing a working webhook with the literal string
 * "********". Leaving a secret field untouched has to mean "keep what is
 * stored", so the mask never travels back.
 *
 * Scope is the other half. A channel is either "all printers" or an explicit
 * list, and those are different states, not the same state with a shortcut:
 * dropping the distinction turns "alert me about the resin printer" into "alert
 * me about everything", which is how people turn notifications off entirely.
 *
 * A repeatedly failing channel disables itself, and the panel must say *that*
 * rather than "disabled" — the operator who turned it off knows; the operator
 * whose webhook host went away does not, and would otherwise be waiting on
 * alerts that stopped days ago.
 *
 * The test button reports the real outcome. Reporting "sent" for a delivery the
 * server could not make is worse than not offering the button.
 */

import "@testing-library/jest-dom/vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NotificationsPanel, type NotificationsPanelDeps } from "@/components/notifications-panel";
import { aPrinter } from "@/test-support/factories";
import { renderApp } from "@/test-support/render";
import type {
  NotificationChannel,
  NotificationChannelCreate,
  NotificationChannelUpdate,
  NotificationDelivery,
  NotificationsSettings,
  NotificationTestResult,
  PrinterRead,
} from "@/types";

const FROZEN_NOW = "2026-01-01T00:00:00Z";

function aChannel(over: Partial<NotificationChannel> = {}): NotificationChannel {
  return {
    id: 3,
    name: "Workshop webhook",
    target: "webhook",
    enabled: true,
    // As the server returns it: the URL is a secret, so it comes back masked.
    config: { url: "********" },
    config_flags: { has_url: true },
    events: ["print_completed", "print_failed"],
    printer_ids: null,
    last_status: "sent",
    last_error: null,
    last_delivered_at: FROZEN_NOW,
    consecutive_failures: 0,
    ...over,
  };
}

function aDelivery(over: Partial<NotificationDelivery> = {}): NotificationDelivery {
  return {
    id: 90,
    channel_id: 3,
    event_type: "print_completed",
    printer_id: 4,
    status: "sent",
    attempts: 1,
    last_error: null,
    created_at: FROZEN_NOW,
    delivered_at: FROZEN_NOW,
    ...over,
  };
}

/**
 * The panel declares every outbound call as a port, toast included, so a test
 * drives it with fakes rather than replacing the modules underneath.
 */
function stubDeps(over: Partial<NotificationsPanelDeps> = {}): NotificationsPanelDeps {
  return {
    getNotificationsSettings: vi
      .fn<() => Promise<NotificationsSettings>>()
      .mockResolvedValue({ enabled: true, channels: [aChannel()] }),
    setNotificationsEnabled: vi
      .fn<(enabled: boolean) => Promise<NotificationsSettings>>()
      .mockResolvedValue({ enabled: false, channels: [] }),
    createNotificationChannel: vi
      .fn<(body: NotificationChannelCreate) => Promise<NotificationChannel>>()
      .mockResolvedValue(aChannel()),
    updateNotificationChannel: vi
      .fn<(id: number, body: NotificationChannelUpdate) => Promise<NotificationChannel>>()
      .mockResolvedValue(aChannel()),
    deleteNotificationChannel: vi.fn<(id: number) => Promise<void>>().mockResolvedValue(undefined),
    testNotificationChannel: vi
      .fn<(id: number) => Promise<NotificationTestResult>>()
      .mockResolvedValue({ ok: true, error: null }),
    listNotificationDeliveries: vi
      .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
      .mockResolvedValue([aDelivery()]),
    listPrinters: vi
      .fn<() => Promise<PrinterRead[]>>()
      .mockResolvedValue([aPrinter({ id: 4, name: "Voron" })]),
    toast: {
      // `toast.error` takes whatever was thrown, so its parameter is genuinely
      // untyped at the call site.
      // oxlint-disable-next-line anti-slop/no-unknown-in-signatures
      error: vi.fn<(cause: unknown) => void>(),
      success: vi.fn<(message: string) => void>(),
      warning: vi.fn<(message: string, description?: string) => void>(),
    },
    ...over,
  };
}

function renderPanel(over: Partial<NotificationsPanelDeps> = {}, canEdit = true) {
  const deps = stubDeps(over);
  const result = renderApp(<NotificationsPanel canEdit={canEdit} deps={deps} />);
  return { ...result, deps };
}

/** Open the create-a-channel form. */
async function openDraft(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /Add channel/ }));
}

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("NotificationsPanel", () => {
  describe("the master switch", () => {
    it("waits for the settings before rendering a state", () => {
      // Painting "Off" and correcting it makes a working setup flash as broken.
      renderPanel({
        getNotificationsSettings: vi
          .fn<() => Promise<NotificationsSettings>>()
          .mockReturnValue(new Promise(() => {})),
      });

      expect(screen.getByText("Loading…")).toBeInTheDocument();
    });

    it("reads as on when notifications are enabled", async () => {
      renderPanel();

      expect(await screen.findByRole("checkbox")).toBeChecked();
    });

    it("turns notifications off when the operator asks", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();

      await user.click(await screen.findByRole("checkbox"));

      await waitFor(() => expect(deps.setNotificationsEnabled).toHaveBeenCalledWith(false));
    });

    it("leaves the switch alone when the server refuses", async () => {
      // A switch showing "Off" over a server that never turned it off is a lie
      // the operator only finds out about via an alert that still arrives.
      const user = userEvent.setup();
      renderPanel({
        setNotificationsEnabled: vi
          .fn<(enabled: boolean) => Promise<NotificationsSettings>>()
          .mockRejectedValue(new Error("nope")),
      });
      const toggle = await screen.findByRole("checkbox");

      await user.click(toggle);

      await waitFor(() => expect(toggle).toBeChecked());
    });
  });

  describe("the channels already configured", () => {
    it("names each channel", async () => {
      renderPanel();

      expect(await screen.findByText("Workshop webhook")).toBeInTheDocument();
    });

    it("says which events it fires on", async () => {
      renderPanel();

      expect(await screen.findByText(/Print completed, Print failed/)).toBeInTheDocument();
    });

    it("says a channel covers every printer", async () => {
      renderPanel();

      expect(await screen.findByText(/all printers/)).toBeInTheDocument();
    });

    it("says how many printers a scoped channel covers", async () => {
      // "All printers" and "these two" are different grants; collapsing them is
      // how an alert arrives about a printer the operator never subscribed to.
      renderPanel({
        getNotificationsSettings: vi
          .fn<() => Promise<NotificationsSettings>>()
          .mockResolvedValue({ enabled: true, channels: [aChannel({ printer_ids: [4, 5] })] }),
      });

      expect(await screen.findByText(/2 printer\(s\)/)).toBeInTheDocument();
    });

    it("says so when no channel has been configured", async () => {
      renderPanel({
        getNotificationsSettings: vi
          .fn<() => Promise<NotificationsSettings>>()
          .mockResolvedValue({ enabled: true, channels: [] }),
      });

      expect(await screen.findByText("No notification channels yet")).toBeInTheDocument();
    });

    it("distinguishes a channel that disabled itself from one the operator turned off", async () => {
      // The operator who turned it off knows; the one whose webhook host went
      // away has been waiting on alerts that stopped days ago.
      renderPanel({
        getNotificationsSettings: vi.fn<() => Promise<NotificationsSettings>>().mockResolvedValue({
          enabled: true,
          channels: [aChannel({ enabled: false, consecutive_failures: 5, last_error: "410" })],
        }),
      });

      expect(await screen.findByText("Auto-disabled")).toBeInTheDocument();
    });

    it("marks a channel the operator turned off as simply disabled", async () => {
      renderPanel({
        getNotificationsSettings: vi.fn<() => Promise<NotificationsSettings>>().mockResolvedValue({
          enabled: true,
          channels: [aChannel({ enabled: false, consecutive_failures: 0 })],
        }),
      });

      expect(await screen.findByText("disabled")).toBeInTheDocument();
    });

    it("shows the last delivery outcome", async () => {
      renderPanel();

      expect(await screen.findAllByText("Delivered")).not.toHaveLength(0);
    });

    it("shows a channel whose last delivery failed", async () => {
      renderPanel({
        getNotificationsSettings: vi.fn<() => Promise<NotificationsSettings>>().mockResolvedValue({
          enabled: true,
          channels: [aChannel({ last_status: "failed", last_error: "connection refused" })],
        }),
        listNotificationDeliveries: vi
          .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
          .mockResolvedValue([]),
      });

      expect(await screen.findByText("Failed")).toBeInTheDocument();
    });

    it("shows a channel that has never delivered anything", async () => {
      renderPanel({
        getNotificationsSettings: vi
          .fn<() => Promise<NotificationsSettings>>()
          .mockResolvedValue({ enabled: true, channels: [aChannel({ last_status: null })] }),
        listNotificationDeliveries: vi
          .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
          .mockResolvedValue([]),
      });

      expect(await screen.findByText("—")).toBeInTheDocument();
    });
  });

  describe("creating a channel", () => {
    it("refuses a channel with no name", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);

      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      expect(deps.createNotificationChannel).not.toHaveBeenCalled();
    });

    it("refuses a channel subscribed to nothing", async () => {
      // A channel with no events never fires, so saving one silently produces a
      // channel the operator believes is working.
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");
      await user.click(screen.getByRole("checkbox", { name: "Print completed" }));
      await user.click(screen.getByRole("checkbox", { name: "Print failed" }));

      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      expect(deps.createNotificationChannel).not.toHaveBeenCalled();
    });

    it("creates the channel the operator described", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");
      await user.type(
        screen.getByPlaceholderText("https://example.com/hook"),
        "https://hooks.example/abc",
      );

      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      await waitFor(() =>
        expect(deps.createNotificationChannel).toHaveBeenCalledWith(
          expect.objectContaining({
            name: "Alerts",
            target: "webhook",
            config: { url: "https://hooks.example/abc" },
            events: ["print_completed", "print_failed"],
            printer_ids: null,
          }),
        ),
      );
    });

    it("asks for the fields the chosen target needs", async () => {
      // A Telegram channel needs a bot token and a chat id; showing the webhook
      // form for it collects nothing the server can deliver with.
      const user = userEvent.setup();
      renderPanel();
      await openDraft(user);

      await user.selectOptions(screen.getByRole("combobox"), "telegram");

      expect(screen.getByPlaceholderText("123456:ABC-DEF…")).toBeInTheDocument();
    });

    it("drops config typed for the previous target", async () => {
      // A webhook URL left in a Telegram channel's config is a stale secret the
      // operator cannot see and never meant to store.
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");
      await user.type(screen.getByPlaceholderText("https://example.com/hook"), "https://x.test/h");

      await user.selectOptions(screen.getByRole("combobox"), "ntfy");
      await user.type(screen.getByPlaceholderText("my-printer-alerts"), "prints");
      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      await waitFor(() =>
        expect(deps.createNotificationChannel).toHaveBeenCalledWith(
          expect.objectContaining({ config: { topic: "prints" } }),
        ),
      );
    });

    it("scopes the channel to the printers the operator picked", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");
      await user.click(screen.getByRole("checkbox", { name: "All printers" }));
      await user.click(await screen.findByRole("checkbox", { name: "Voron" }));

      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      await waitFor(() =>
        expect(deps.createNotificationChannel).toHaveBeenCalledWith(
          expect.objectContaining({ printer_ids: [4] }),
        ),
      );
    });

    it("says so when a scoped channel has no printers to pick from", async () => {
      const user = userEvent.setup();
      renderPanel({
        listPrinters: vi.fn<() => Promise<PrinterRead[]>>().mockResolvedValue([]),
      });
      await openDraft(user);

      await user.click(screen.getByRole("checkbox", { name: "All printers" }));

      expect(screen.getByText("No printers configured.")).toBeInTheDocument();
    });

    it("surfaces a channel the server rejected", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel({
        createNotificationChannel: vi
          .fn<(body: NotificationChannelCreate) => Promise<NotificationChannel>>()
          .mockRejectedValue(new Error("invalid_webhook_url")),
      });
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");

      await user.click(screen.getByRole("button", { name: /Create channel/ }));

      await waitFor(() => expect(deps.toast.error).toHaveBeenCalled());
    });

    it("abandons the draft when the operator cancels", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await openDraft(user);
      await user.type(screen.getByPlaceholderText(/Living-room/), "Alerts");

      await user.click(screen.getByRole("button", { name: "Cancel" }));

      expect(deps.createNotificationChannel).not.toHaveBeenCalled();
    });
  });

  describe("editing a channel", () => {
    it("starts a secret field blank rather than holding the mask", async () => {
      // "********" is what the server *shows*, never what it stores. Sending it
      // back replaces a working webhook with eight asterisks.
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByTitle("Edit channel"));

      expect(screen.queryByDisplayValue("********")).toBeNull();
    });

    it("keeps the stored secret when the field is left alone", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await user.click(await screen.findByTitle("Edit channel"));

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

      await waitFor(() =>
        expect(deps.updateNotificationChannel).toHaveBeenCalledWith(
          3,
          expect.objectContaining({ config: {} }),
        ),
      );
    });

    it("saves the rest of what the operator changed", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await user.click(await screen.findByTitle("Edit channel"));
      const name = screen.getByPlaceholderText(/Living-room/);

      await user.clear(name);
      await user.type(name, "Renamed");
      await user.click(screen.getByRole("button", { name: /Save changes/ }));

      await waitFor(() =>
        expect(deps.updateNotificationChannel).toHaveBeenCalledWith(
          3,
          expect.objectContaining({ name: "Renamed" }),
        ),
      );
    });

    it("will not let the target be changed under an existing channel", async () => {
      // The stored config belongs to the old target; switching would leave the
      // channel holding credentials for a service it no longer talks to.
      const user = userEvent.setup();
      renderPanel();

      await user.click(await screen.findByTitle("Edit channel"));

      expect(screen.getByRole("combobox")).toBeDisabled();
    });

    it("re-reads the channels after a save", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();
      await user.click(await screen.findByTitle("Edit channel"));

      await user.click(screen.getByRole("button", { name: /Save changes/ }));

      await waitFor(() => expect(deps.getNotificationsSettings).toHaveBeenCalledTimes(2));
    });
  });

  describe("testing a channel", () => {
    it("asks the server to deliver a test", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();

      await user.click(await screen.findByTitle("Send a test notification"));

      await waitFor(() => expect(deps.testNotificationChannel).toHaveBeenCalledWith(3));
    });

    it("reports a test the server could not deliver", async () => {
      // Reporting "sent" for a delivery that failed is worse than not offering
      // the button at all.
      const user = userEvent.setup();
      const { deps } = renderPanel({
        testNotificationChannel: vi
          .fn<(id: number) => Promise<NotificationTestResult>>()
          .mockResolvedValue({ ok: false, error: "connection refused" }),
      });

      await user.click(await screen.findByTitle("Send a test notification"));

      await waitFor(() =>
        expect(deps.toast.warning).toHaveBeenCalledWith("Test failed", "connection refused"),
      );
    });

    it("confirms a test that went through", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();

      await user.click(await screen.findByTitle("Send a test notification"));

      await waitFor(() =>
        expect(deps.toast.success).toHaveBeenCalledWith("Test notification sent."),
      );
    });
  });

  describe("deleting a channel", () => {
    it("deletes the channel the operator chose", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();

      await user.click(await screen.findByTitle("Delete channel"));

      await waitFor(() => expect(deps.deleteNotificationChannel).toHaveBeenCalledWith(3));
    });

    it("re-reads the channels afterwards", async () => {
      const user = userEvent.setup();
      const { deps } = renderPanel();

      await user.click(await screen.findByTitle("Delete channel"));

      await waitFor(() => expect(deps.getNotificationsSettings).toHaveBeenCalledTimes(2));
    });
  });

  describe("recent deliveries", () => {
    it("lists what was sent lately", async () => {
      renderPanel();

      expect(await screen.findByText("Recent deliveries")).toBeInTheDocument();
    });

    it("shows how many attempts a delivery took", async () => {
      // A delivery that eventually succeeded after four tries is a channel
      // heading for auto-disable, and the retry count is the only warning.
      renderPanel({
        listNotificationDeliveries: vi
          .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
          .mockResolvedValue([aDelivery({ attempts: 4 })]),
      });

      expect(await screen.findByText("×4")).toBeInTheDocument();
    });

    it("shows nothing when nothing has been delivered", async () => {
      renderPanel({
        listNotificationDeliveries: vi
          .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
          .mockResolvedValue([]),
      });

      await screen.findByText("Workshop webhook");
      expect(screen.queryByText("Recent deliveries")).toBeNull();
    });

    it("still renders the panel when the delivery log cannot be read", async () => {
      // The log is context, not the feature; losing it must not take the
      // channels down with it.
      renderPanel({
        listNotificationDeliveries: vi
          .fn<(limit?: number) => Promise<NotificationDelivery[]>>()
          .mockRejectedValue(new Error("boom")),
      });

      expect(await screen.findByText("Workshop webhook")).toBeInTheDocument();
    });
  });

  describe("a viewer who is not an administrator", () => {
    it("says who can manage channels", async () => {
      renderPanel({}, false);

      expect(
        await screen.findByText("Only an administrator can manage notification channels."),
      ).toBeInTheDocument();
    });

    it("offers no channels to manage", async () => {
      renderPanel({}, false);

      await screen.findByText(/Only an administrator/);
      expect(screen.queryByRole("button", { name: /Add channel/ })).toBeNull();
    });

    it("offers no master switch to flip", async () => {
      renderPanel({}, false);

      expect(await screen.findByRole("checkbox")).toBeDisabled();
    });
  });

  describe("when the settings cannot be read", () => {
    it("stops loading rather than hanging", async () => {
      const { deps } = renderPanel({
        getNotificationsSettings: vi
          .fn<() => Promise<NotificationsSettings>>()
          .mockRejectedValue(new Error("boom")),
      });

      await waitFor(() => expect(deps.toast.error).toHaveBeenCalled());
      expect(screen.queryByText("Loading…")).toBeNull();
    });
  });
});
