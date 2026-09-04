/**
 * The builders are load-bearing, so their promises are tested like anything else.
 *
 * Every frontend test that needs an API-shaped object gets it from here, which
 * makes a wrong builder worse than a wrong test: it is wrong everywhere at once,
 * and quietly. The promises worth pinning are that a builder returns the
 * *ordinary* state (so an interesting variant is visible at the call site), that
 * an override replaces only what it names, and that two builds never share a
 * nested object — a shared one lets one test's mutation leak into the next.
 */

import { describe, expect, it } from "vitest";

import {
  FROZEN_NOW,
  aModelListItem,
  aPrinter,
  aPrintJob,
  printerAccess,
  printerCapabilities,
} from "@/test-support/factories";

describe("aPrinter", () => {
  it("returns a reachable printer the caller may operate", () => {
    const printer = aPrinter();

    // The ordinary case. A test asserting a control is hidden has to turn
    // something off, which is exactly the signal a reader wants.
    expect(printer.status).toBe("ready");
    expect(printer.access.can_print).toBe(true);
    expect(printer.capabilities.can_start).toBe(true);
  });

  it("applies a top-level override", () => {
    expect(aPrinter({ status: "printing" }).status).toBe("printing");
  });

  it("takes a composed access block without dropping its siblings", () => {
    const printer = aPrinter({ access: printerAccess({ can_print: false }) });

    // Composition rather than a deep merge: naming one permission must not
    // blank the other four, or every RBAC test would silently lose its context.
    expect(printer.access.can_print).toBe(false);
    expect(printer.access.can_view).toBe(true);
    expect(printer.access.can_admin).toBe(true);
  });

  it("takes a composed capabilities block", () => {
    const printer = aPrinter({
      capabilities: printerCapabilities({ unsupported_actions: ["pause"] }),
    });

    expect(printer.capabilities.unsupported_actions).toEqual(["pause"]);
    expect(printer.capabilities.can_start).toBe(true);
  });

  it("does not share nested objects between two builds", () => {
    const first = aPrinter();
    const second = aPrinter();

    first.capabilities.can_start = false;

    // A shared nested object would let one test's mutation leak into the next,
    // which is an order-dependent failure a long way from its cause.
    expect(second.capabilities.can_start).toBe(true);
  });

  it("uses a fixed instant for every timestamp", () => {
    const printer = aPrinter();

    // Never `Date.now()`: a clock-derived fixture makes a relative-time
    // assertion pass only on the day it was written.
    expect(printer.created_at).toBe(FROZEN_NOW);
    expect(printer.updated_at).toBe(FROZEN_NOW);
  });
});

describe("printerAccess", () => {
  it("grants everything by default", () => {
    expect(printerAccess().can_admin).toBe(true);
  });

  it("narrows to the role a permission test needs", () => {
    const access = printerAccess({ role: "view", can_print: false, can_admin: false });

    expect(access).toMatchObject({ role: "view", can_print: false, can_view: true });
  });
});

describe("printerCapabilities", () => {
  it("enables every capability at stable support", () => {
    const capabilities = printerCapabilities();

    expect(capabilities.support_level).toBe("stable");
    expect(capabilities.can_send_gcode).toBe(true);
  });

  it("turns off only the capability named", () => {
    const capabilities = printerCapabilities({ can_pause: false });

    expect(capabilities.can_pause).toBe(false);
    expect(capabilities.can_resume).toBe(true);
  });
});

describe("aPrintJob", () => {
  it("returns a queued, vault-backed job", () => {
    const job = aPrintJob();

    // `vault` means PrintStash owns the bytes, which is the ordinary case; the
    // other evidence values describe a job seen on the printer that we could
    // not capture, and a test asks for those by name.
    expect(job.state).toBe("queued");
    expect(job.artifact_evidence).toBe("vault");
  });

  it("applies an override", () => {
    expect(aPrintJob({ state: "printing", progress: 0.5 }).progress).toBe(0.5);
  });
});

describe("aModelListItem", () => {
  it("returns a row with nothing printed yet", () => {
    const item = aModelListItem();

    expect(item.print_summary).toBeNull();
    expect(item.starred).toBe(false);
  });

  it("applies an override", () => {
    expect(aModelListItem({ name: "Gearbox" }).name).toBe("Gearbox");
  });
});
