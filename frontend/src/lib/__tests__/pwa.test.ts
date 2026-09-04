/*
 * The service worker, which is the one part of the app a bad deploy can pin
 * forever.
 *
 * A service worker that caches its own registration script serves the *previous*
 * version of itself on every visit, so a fix ships and nobody receives it. That
 * is why the registration bypasses the HTTP cache and the caches are versioned:
 * a new release must be able to evict the old one.
 *
 * The reload is latched to once. A worker taking control fires the change event,
 * and reloading unconditionally on every such event is an infinite refresh loop —
 * the user sees the page flicker and can never interact with it.
 *
 * The offline navigation fallback is what makes the app openable on a phone in a
 * workshop with no signal, which is a real place people use PrintStash.
 */

import { createControllerChangeHandler, registerPwa } from "@/lib/pwa";
import { readFileSync } from "node:fs";
import { describe, expect, it, vi } from "vitest";

/** The only message `registerPwa` posts to a waiting worker. */
type SkipWaitingMessage = { type: "SKIP_WAITING" };

/** The slice of `ServiceWorkerRegistration` that `registerPwa` actually touches. */
type StubRegistration = {
  update: () => Promise<void>;
  waiting: { postMessage: (message: SkipWaitingMessage) => void };
};

type RegisterStub = (scriptURL: string, options?: RegistrationOptions) => Promise<StubRegistration>;
type AddEventListenerStub = (type: string, listener: EventListenerOrEventListenerObject) => void;

describe("registerServiceWorker", () => {
  it("registers and checks the production service worker without HTTP cache", async () => {
    const update = vi.fn<StubRegistration["update"]>().mockResolvedValue(undefined);
    const waiting = {
      postMessage: vi.fn<StubRegistration["waiting"]["postMessage"]>(),
    };
    const register = vi.fn<RegisterStub>().mockResolvedValue({ update, waiting });
    const addEventListener = vi.fn<AddEventListenerStub>();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { addEventListener, register },
    });

    registerPwa(true);
    window.dispatchEvent(new Event("load"));
    await vi.waitFor(() => expect(update).toHaveBeenCalled());

    expect(register).toHaveBeenCalledWith("/sw.js", {
      scope: "/",
      updateViaCache: "none",
    });
    expect(waiting.postMessage).toHaveBeenCalledWith({ type: "SKIP_WAITING" });
    expect(addEventListener).toHaveBeenCalledWith("controllerchange", expect.any(Function));
  });

  it("does not register when disabled", () => {
    const register = vi.fn<RegisterStub>();
    Object.defineProperty(navigator, "serviceWorker", {
      configurable: true,
      value: { register },
    });

    registerPwa(false);
    window.dispatchEvent(new Event("load"));
    expect(register).not.toHaveBeenCalled();
  });

  it("reloads once when an updated service worker takes control", () => {
    const reload = vi.fn<() => void>();
    const handleControllerChange = createControllerChangeHandler(reload);

    handleControllerChange();
    handleControllerChange();

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it("uses versioned caches, offline navigation fallback, and revalidation", () => {
    const source = readFileSync(`${process.cwd()}/public/sw.js`, "utf8");

    expect(source).toContain('const CACHE = "printstash-shell-v3"');
    expect(source).toContain('caches.match("/offline.html")');
    expect(source).toContain("event.waitUntil(network.catch");
    expect(source).toContain('event.data?.type === "SKIP_WAITING"');
  });
});
