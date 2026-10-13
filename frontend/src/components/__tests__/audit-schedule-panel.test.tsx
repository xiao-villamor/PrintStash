/*
 * Scheduled audit policy controls are durable operational settings. These tests
 * defend compare-and-set saves, safe deferral copy, and recoverable failures.
 */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { clearLogin } from "@/lib/auth-store";
import { AuditSchedulePanel } from "@/components/audit-schedule-panel";
import { anAuditPolicy } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

describe("Audit schedules", () => {
  it("retires a schedule command on session change", async () => {
    const pending = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([
          anAuditPolicy({ enabled: true, next_due_at: "2026-10-11T02:00:00Z" }),
        ]),
        "GET /api/v1/maintenance/audits": json([]),
        "POST /api/v1/maintenance/audit-policies/quick/skip": (_url, init) => {
          signal = init?.signal;
          return pending.promise;
        },
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: /Skip/ }));
    await act(async () => clearLogin());
    expect(signal?.aborted).toBe(true);
    await act(async () => pending.resolve(json(anAuditPolicy({ enabled: true, revision: 2 }))));
    expect(view.client.getQueryData(["maintenance", "policies"])).toBeUndefined();
    expect(screen.queryByRole("form", { name: "Quick check schedule" })).not.toBeInTheDocument();
  });
  it("preserves a newer schedule observed during a skip", async () => {
    const pending = Promise.withResolvers<Response>();
    const full = anAuditPolicy({ mode: "full", enabled: true, timezone: "Europe/Rome" });
    const latest = anAuditPolicy({
      enabled: true,
      revision: 3,
      timezone: "Europe/Paris",
      next_due_at: "2026-10-18T02:00:00Z",
    });
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([
          anAuditPolicy({ enabled: true, next_due_at: "2026-10-11T02:00:00Z" }),
          full,
        ]),
        "GET /api/v1/maintenance/audits": json([]),
        "POST /api/v1/maintenance/audit-policies/quick/skip": () => pending.promise,
      },
    });
    const quick = await screen.findByRole("form", { name: "Quick check schedule" });
    await userEvent.click(within(quick).getByRole("button", { name: /Skip/ }));
    view.route({ "GET /api/v1/maintenance/audit-policies": json([latest, full]) });
    await act(async () => {
      await view.client.invalidateQueries({ queryKey: ["maintenance", "policies"] });
    });
    await waitFor(() =>
      expect(within(quick).getByLabelText("Time zone")).toHaveValue("Europe/Paris"),
    );
    await act(async () =>
      pending.resolve(json(anAuditPolicy({ enabled: true, revision: 2, timezone: "UTC" }))),
    );
    await waitFor(() => expect(within(quick).getByRole("button", { name: /Skip/ })).toBeEnabled());
    expect(within(quick).getByLabelText("Time zone")).toHaveValue("Europe/Paris");
    expect(
      within(screen.getByRole("form", { name: "Full check schedule" })).getByLabelText("Time zone"),
    ).toHaveValue("Europe/Rome");
  });
  it("recovers a schedule catalog failure without losing draft", async () => {
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy({ enabled: true })]),
        "GET /api/v1/maintenance/audits": json([]),
      },
    });
    const input = await screen.findByLabelText("Time zone");
    await userEvent.clear(input);
    await userEvent.type(input, "Europe/Madrid");
    view.route({ "GET /api/v1/maintenance/audit-policies": json({ detail: "unavailable" }, 503) });
    await act(async () => {
      await view.client.invalidateQueries();
    });
    const retry = await screen.findByRole("button", { name: "Retry" });
    expect(input).toHaveValue("Europe/Madrid");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    view.route({
      "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy({ enabled: true })]),
    });
    await userEvent.click(retry);
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    expect(input).toHaveValue("Europe/Madrid");
  });
  it("retains a schedule draft during catalog refresh", async () => {
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy({ enabled: true })]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json(
          anAuditPolicy({ enabled: true, revision: 3 }),
        ),
      },
    });
    const input = await screen.findByLabelText("Time zone");
    await userEvent.clear(input);
    await userEvent.type(input, "Europe/Madrid");
    view.route({
      "GET /api/v1/maintenance/audit-policies": json([
        anAuditPolicy({ enabled: true, revision: 2, timezone: "Europe/Paris" }),
      ]),
    });
    await act(async () => {
      await view.client.invalidateQueries();
    });
    await waitFor(() =>
      expect(
        view.requestsWithMethod("GET").filter((request) => request.url.endsWith("/audit-policies")),
      ).toHaveLength(2),
    );
    expect(input).toHaveValue("Europe/Madrid");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"expected_revision":1');
  });
  it("retains a schedule draft when skipping a slot", async () => {
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([
          anAuditPolicy({ enabled: true, next_due_at: "2026-10-11T02:00:00Z" }),
        ]),
        "GET /api/v1/maintenance/audits": json([]),
        "POST /api/v1/maintenance/audit-policies/quick/skip": json(
          anAuditPolicy({ enabled: true, revision: 2, next_due_at: "2026-10-18T02:00:00Z" }),
        ),
      },
    });
    const input = await screen.findByLabelText("Time zone");
    await userEvent.clear(input);
    await userEvent.type(input, "Europe/Madrid");
    await userEvent.click(screen.getByRole("button", { name: /Skip/ }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save changes" })).toBeEnabled());
    expect(input).toHaveValue("Europe/Madrid");
  });
  it.each([409, 503])("requires review after an unaccepted schedule save (%i)", async (status) => {
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy({ enabled: true })]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json({ detail: "edit_conflict" }, status),
      },
    });
    const input = await screen.findByLabelText("Time zone");
    await userEvent.clear(input);
    await userEvent.type(input, "Europe/Madrid");
    await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("button", { name: "Review current values" })).toBeVisible();
    expect(input).toHaveValue("Europe/Madrid");
    expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
    view.route({
      "GET /api/v1/maintenance/audit-policies": json([
        anAuditPolicy({ enabled: true, revision: 2, timezone: "Europe/Paris" }),
      ]),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review current values" }));
    await userEvent.click(await screen.findByRole("button", { name: "Use current values" }));
    expect(input).toHaveValue("Europe/Paris");
    expect(view.requestsWithMethod("PUT")).toHaveLength(1);
  });
  it("shows only the scheduling switch until a weekly check is enabled", async () => {
    const user = userEvent.setup();
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy()]),
        "GET /api/v1/maintenance/audits": json([]),
      },
    });
    const form = await screen.findByRole("form", { name: "Quick check schedule" });
    expect(within(form).queryByLabelText("Frequency")).not.toBeInTheDocument();
    expect(within(form).queryByRole("button", { name: "Save changes" })).not.toBeInTheDocument();

    await user.click(within(form).getByRole("checkbox", { name: "Run automatically" }));
    expect(within(form).getByLabelText("Frequency")).toBeVisible();
    expect(within(form).getByLabelText("Day of week")).toHaveValue("6");
    expect(within(form).getByRole("option", { name: "Sunday" })).toBeInTheDocument();
    expect(within(form).getByRole("button", { name: "Save changes" })).toBeVisible();
  });

  it("saves an enabled schedule", async () => {
    const user = userEvent.setup();
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy()]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json(
          anAuditPolicy({ enabled: true, next_due_at: "2026-09-13T02:00:00Z", revision: 2 }),
        ),
      },
    });
    const form = await screen.findByRole("form", { name: "Quick check schedule" });
    await user.click(within(form).getByRole("checkbox", { name: "Run automatically" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"enabled":true'),
    );
    expect(view.requestsWithMethod("PUT")[0]?.body).not.toContain('"revision"');
    expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"expected_revision":1');
  });

  it("saves the configured policy controls", async () => {
    const user = userEvent.setup();
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy()]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json(anAuditPolicy({ revision: 2 })),
      },
    });
    const form = await screen.findByRole("form", { name: "Quick check schedule" });
    await user.click(within(form).getByRole("checkbox", { name: "Run automatically" }));
    await user.selectOptions(within(form).getByLabelText("Frequency"), "monthly");
    await user.click(within(form).getByText("Advanced settings"));
    await user.selectOptions(
      within(form).getByLabelText("Issue notification threshold"),
      "critical",
    );
    await user.clear(within(form).getByLabelText("Overdue after (minutes)"));
    await user.type(within(form).getByLabelText("Overdue after (minutes)"), "45");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() =>
      expect(view.requestsWithMethod("PUT")[0]?.body).toContain(
        '"notification_threshold":"critical"',
      ),
    );
    expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"cadence":"monthly"');
    expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"max_lateness_minutes":45');
  });

  it("saves a paused schedule", async () => {
    const user = userEvent.setup();
    const view = renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy({ enabled: true })]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json(
          anAuditPolicy({ enabled: true, paused: true }),
        ),
      },
    });
    const form = await screen.findByRole("form", { name: "Quick check schedule" });
    await user.click(within(form).getByText("Advanced settings"));
    await user.click(within(form).getByRole("checkbox", { name: "Paused" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(view.requestsWithMethod("PUT")[0]?.body).toContain('"paused":true'));
  });

  it("renders a safe deferred explanation", async () => {
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([
          anAuditPolicy({ deferred_reason: "storage_unavailable" }),
        ]),
        "GET /api/v1/maintenance/audits": json([]),
      },
    });
    expect(await screen.findByText(/Storage is unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/storage_unavailable/)).not.toBeInTheDocument();
  });

  it("shows a recoverable loading failure", async () => {
    renderApp(<AuditSchedulePanel />);
    expect(await screen.findByText("Could not load audit schedules.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
  });

  it("keeps schedules available when check history fails", async () => {
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy()]),
        "GET /api/v1/maintenance/audits": json({ detail: "unavailable" }, 503),
      },
    });
    expect(await screen.findByRole("form", { name: "Quick check schedule" })).toBeVisible();
    expect(await screen.findByRole("alert")).toHaveTextContent("Check history could not be loaded");
  });

  it("shows the full-check cost acknowledgement without opening advanced settings", async () => {
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([
          anAuditPolicy({ mode: "full", enabled: true }),
        ]),
        "GET /api/v1/maintenance/audits": json([]),
      },
    });
    const form = await screen.findByRole("form", { name: "Full check schedule" });
    expect(within(form).getByText(/Full audits read all owned data/)).toBeVisible();
  });

  it("keeps an unsuccessful save editable", async () => {
    const user = userEvent.setup();
    renderApp(<AuditSchedulePanel />, {
      routes: {
        "GET /api/v1/maintenance/audit-policies": json([anAuditPolicy()]),
        "GET /api/v1/maintenance/audits": json([]),
        "PUT /api/v1/maintenance/audit-policies/quick": json({ detail: "invalid" }, 400),
      },
    });
    const form = await screen.findByRole("form", { name: "Quick check schedule" });
    await user.click(within(form).getByRole("checkbox", { name: "Run automatically" }));
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Could not save audit schedule");
    expect(within(form).getByRole("button", { name: "Save changes" })).toBeEnabled();
  });
});
