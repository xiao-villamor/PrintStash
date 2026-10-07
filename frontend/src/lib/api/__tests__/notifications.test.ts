/**
 * Notification settings, the channels that deliver them, and the delivery log.
 *
 * A channel is a place the server will send outbound traffic to on the user's
 * behalf, so each act on one is its own endpoint rather than a flag on the parent:
 * creating, editing, deleting, and *testing* a channel are separately auditable
 * that way. The test endpoint in particular has to hang off the channel it tests —
 * sending a probe through the wrong channel is a message to a third party.
 *
 * The delivery log is paginated with an explicit default, because "everything ever
 * delivered" grows without bound and this list is rendered in a settings panel.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationsSettings,
  listNotificationDeliveries,
  setNotificationsEnabled,
  testNotificationChannel,
  updateNotificationChannel,
} from "@/lib/api/notifications";

import { expectRequest, fetchMock, lastBody, respondWith } from "./_wire";

const BASE = { edit_epoch: "a".repeat(32), edit_version: 1 };

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();

  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getNotificationsSettings", () => {
  it("reads current settings on every transport call", async () => {
    respondWith({ enabled: false, channels: [] });
    await getNotificationsSettings();
    respondWith({ enabled: true, channels: [] });

    const current = await getNotificationsSettings();

    expect(current).toEqual({ enabled: true, channels: [] });
  });

  it("reads the settings", async () => {
    respondWith({ enabled: false, channels: [] });

    await getNotificationsSettings();

    expectRequest("/api/v1/notifications");
  });
});

describe("setNotificationsEnabled", () => {
  it("sends the captured switch precondition", async () => {
    respondWith({ ...BASE, edit_version: 2, enabled: true });

    await setNotificationsEnabled(true, { base: BASE });

    expect(fetchMock.mock.calls.at(-1)?.[1]?.headers).toMatchObject({
      "If-Match": `"notification-settings-e${BASE.edit_epoch}-v1"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    });
  });

  it("PUTs the enabled flag", async () => {
    respondWith({ ...BASE, edit_version: 2, enabled: true });

    await setNotificationsEnabled(true, { base: BASE });

    expectRequest("/api/v1/notifications", "PUT");
    expect(lastBody()).toEqual({ enabled: true });
  });
});

describe("createNotificationChannel", () => {
  it("POSTs a new channel", async () => {
    respondWith({ id: 1, target: "webhook" });

    await createNotificationChannel({
      name: "Ops webhook",
      target: "webhook",
      config: { url: "https://hooks.test/x" },
      events: ["print_completed"],
    });

    expectRequest("/api/v1/notifications/channels", "POST");
  });
});

describe("updateNotificationChannel", () => {
  it("sends the captured channel precondition", async () => {
    respondWith({ ...BASE, edit_version: 2, id: 1 });

    await updateNotificationChannel(1, { enabled: false }, { base: BASE });

    expect(fetchMock.mock.calls.at(-1)?.[1]?.headers).toMatchObject({
      "If-Match": `"notification-channel-1-e${BASE.edit_epoch}-v1"`,
      "X-PrintStash-Edit-Contract": "conditional-v1",
    });
  });

  it.each([
    {
      label: "wrong history",
      receipt: { ...BASE, edit_epoch: "b".repeat(32), edit_version: 2, id: 1 },
      error: "Invalid editing acknowledgement",
    },
    {
      label: "unadvanced version",
      receipt: { ...BASE, id: 1 },
      error: "Invalid editing acknowledgement",
    },
    {
      label: "wrong identity",
      receipt: { ...BASE, edit_version: 2, id: 2 },
      error: "notification_identity_mismatch",
    },
  ])("rejects invalid notification acknowledgements: $label", async ({ receipt, error }) => {
    respondWith(receipt);

    await expect(updateNotificationChannel(1, { enabled: false }, { base: BASE })).rejects.toThrow(
      error,
    );
  });

  it("PATCHes only what changed", async () => {
    respondWith({ ...BASE, edit_version: 2, id: 1, target: "webhook" });

    await updateNotificationChannel(1, { enabled: false }, { base: BASE });

    expectRequest("/api/v1/notifications/channels/1", "PATCH");
  });
});

describe("deleteNotificationChannel", () => {
  it("deletes one by id", async () => {
    respondWith(null, 204);

    await deleteNotificationChannel(1);

    expectRequest("/api/v1/notifications/channels/1", "DELETE");
  });
});

describe("testNotificationChannel", () => {
  it("sends a test through the channel's own endpoint", async () => {
    respondWith({ ok: true });

    await testNotificationChannel(1);

    expectRequest("/api/v1/notifications/channels/1/test", "POST");
  });
});

describe("listNotificationDeliveries", () => {
  it("reads current deliveries on every transport call", async () => {
    respondWith([{ id: 3 }]);
    await listNotificationDeliveries();
    respondWith([]);

    const current = await listNotificationDeliveries();

    expect(current).toEqual([]);
  });

  it("asks for a default page", async () => {
    respondWith([]);

    await listNotificationDeliveries();

    expectRequest("/api/v1/notifications/deliveries?limit=50");
  });

  it("asks for the page the caller wants", async () => {
    respondWith([]);

    await listNotificationDeliveries(5);

    expectRequest("/api/v1/notifications/deliveries?limit=5");
  });
});
