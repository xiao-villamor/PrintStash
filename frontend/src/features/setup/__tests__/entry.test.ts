/** Setup bootstrap is a closed Query projection; credential commands retain scoped failure contracts. */
import { describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import {
  setupEntryOptions,
  completeSetupEntry,
  checkSetupEntry,
  type SetupEntryApi,
} from "@/features/setup/entry";
import {
  beginSetup,
  completeSetup,
  checkSetupStorage,
  getSetupStatus,
  getStorageProviders,
} from "@/lib/api/config";
import { getUser, retirePrivateSessionScope } from "@/lib/auth-store";
import { queryClient } from "@/lib/query-client";
import { json, renderApp } from "@/test-support/render";

const liveApi: SetupEntryApi = {
  beginSetup,
  completeSetup,
  checkSetupStorage,
  getSetupStatus,
  getStorageProviders,
};

describe("setup bootstrap policy", () => {
  it("stops configured bootstrap before session preparation", async () => {
    const api = {
      ...liveApi,
      getSetupStatus: vi
        .fn<SetupEntryApi["getSetupStatus"]>()
        .mockResolvedValue({ configured: true, user_count: 1 }),
      beginSetup: vi.fn<SetupEntryApi["beginSetup"]>(),
      getStorageProviders: vi.fn<SetupEntryApi["getStorageProviders"]>(),
    };

    const result = await queryClient.fetchQuery(setupEntryOptions(api, "configured-entry", 0));

    expect(result).toEqual({ kind: "configured", status: { configured: true, user_count: 1 } });
    expect(api.beginSetup).not.toHaveBeenCalled();
    expect(api.getStorageProviders).not.toHaveBeenCalled();
  });

  it("stops unavailable bootstrap before session preparation", async () => {
    const api = {
      ...liveApi,
      getSetupStatus: vi
        .fn<SetupEntryApi["getSetupStatus"]>()
        .mockResolvedValue({ configured: false, user_count: 0, setup_available: false }),
      beginSetup: vi.fn<SetupEntryApi["beginSetup"]>(),
      getStorageProviders: vi.fn<SetupEntryApi["getStorageProviders"]>(),
    };

    const result = await queryClient.fetchQuery(setupEntryOptions(api, "unavailable-entry", 0));

    expect(result).toEqual({
      kind: "unavailable",
      status: { configured: false, user_count: 0, setup_available: false },
    });
    expect(api.beginSetup).not.toHaveBeenCalled();
    expect(api.getStorageProviders).not.toHaveBeenCalled();
  });

  it("preserves a genuine unauthorized setup completion failure", async () => {
    const view = renderApp(createElement("div"), {
      routes: {
        "POST /api/v1/setup/session": json({ csrf: "automatic-proof", expires_in: 3600 }),
        "POST /api/v1/setup": json({ detail: "setup_session_expired" }, 401),
      },
    });

    await expect(
      completeSetupEntry(
        liveApi,
        { username: "maker", password: "password", storage_provider: "local" },
        new AbortController().signal,
      ),
    ).rejects.toMatchObject({ status: 401, code: "setup_session_expired" });

    expect(view.requestsWithMethod("GET")).toEqual([]);
    expect(getUser()).toBeNull();
  });
});

describe("setup command scope", () => {
  it.each([{ command: "check" }, { command: "create" }])(
    "stops a $command command after private scope retirement",
    async ({ command }) => {
      let finishSession = () => {};
      const api = {
        ...liveApi,
        beginSetup: vi.fn<SetupEntryApi["beginSetup"]>().mockImplementation(
          () =>
            new Promise((resolve) => {
              finishSession = () => resolve({ csrf: "automatic-proof", expires_in: 3600 });
            }),
        ),
        checkSetupStorage: vi.fn<SetupEntryApi["checkSetupStorage"]>(),
        completeSetup: vi.fn<SetupEntryApi["completeSetup"]>(),
      };
      const signal = new AbortController().signal;
      const pending =
        command === "check"
          ? checkSetupEntry(api, { storage_provider: "local" }, signal)
          : completeSetupEntry(
              api,
              { username: "maker", password: "password", storage_provider: "local" },
              signal,
            );

      retirePrivateSessionScope();
      finishSession();

      await expect(pending).rejects.toMatchObject({ name: "AbortError" });
      expect(api.checkSetupStorage).not.toHaveBeenCalled();
      expect(api.completeSetup).not.toHaveBeenCalled();
    },
  );

  it("suppresses recovery after private scope retirement", async () => {
    let failCompletion = () => {};
    let completionStarted = () => {};
    const started = new Promise<void>((resolve) => {
      completionStarted = resolve;
    });
    const api = {
      ...liveApi,
      beginSetup: vi
        .fn<SetupEntryApi["beginSetup"]>()
        .mockResolvedValue({ csrf: "automatic-proof", expires_in: 3600 }),
      completeSetup: vi.fn<SetupEntryApi["completeSetup"]>().mockImplementation(
        () =>
          new Promise((_, reject) => {
            failCompletion = () => reject(new Error("lost acknowledgement"));
            completionStarted();
          }),
      ),
      getSetupStatus: vi.fn<SetupEntryApi["getSetupStatus"]>(),
    };
    const pending = completeSetupEntry(
      api,
      { username: "maker", password: "password", storage_provider: "local" },
      new AbortController().signal,
    );
    await started;

    retirePrivateSessionScope();
    failCompletion();

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(api.getSetupStatus).not.toHaveBeenCalled();
  });
});
