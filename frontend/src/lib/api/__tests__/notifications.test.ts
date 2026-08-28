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
import { invalidateApiCache } from "@/lib/api/request";

import { expectRequest, fetchMock, lastBody, respondWith } from "./_wire";

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset();
  invalidateApiCache();
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("getNotificationsSettings", () => {
  it("reads the settings", async () => {
    respondWith({ enabled: false, channels: [] });

    await getNotificationsSettings();

    expectRequest("/api/v1/notifications");
  });
});

describe("setNotificationsEnabled", () => {
  it("PUTs the enabled flag", async () => {
    respondWith({ enabled: true });

    await setNotificationsEnabled(true);

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
  it("PATCHes only what changed", async () => {
    respondWith({ id: 1, target: "webhook" });

    await updateNotificationChannel(1, { enabled: false });

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
