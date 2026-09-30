/**
 * Retained input is deleted only after confirmation through the real API client.
 * Ownership conflicts remain visible and do not dismiss the recovery controls.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { StagedInputRecovery } from "@/components/staged-input-recovery";

const fetchMock = vi.fn<typeof fetch>();
const staging = {
  retained_bytes: 1024,
  lease_count: 1,
  earliest_expiry: "2026-10-01T12:00:00Z",
  discard_available: true,
};

describe("StagedInputRecovery", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("shows retained capacity", () => {
    render(<StagedInputRecovery jobId="job-1" staging={staging} onDiscard={() => {}} />);
    expect(screen.getByText(/Retained input:.*1.*staged files/)).toBeVisible();
  });

  it("waits for confirmation before releasing input", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    render(<StagedInputRecovery jobId="job/1" staging={staging} onDiscard={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Discard staged input" }));
    expect(fetchMock).not.toHaveBeenCalled();
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Discard staged input" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    expect(fetchMock.mock.calls[0]?.[0]).toContain("/jobs/job%2F1/discard-staging");
    expect(fetchMock.mock.calls[0]?.[1]?.method).toBe("POST");
  });

  it("keeps an ownership conflict visible", async () => {
    const user = userEvent.setup();
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "staging_ownership_uncertain" }), { status: 409 }),
    );
    render(<StagedInputRecovery jobId="job-1" staging={staging} onDiscard={() => {}} />);
    await user.click(screen.getByRole("button", { name: "Discard staged input" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Discard staged input" }),
    );
    expect(await screen.findByRole("alert")).toBeVisible();
    expect(screen.getByRole("dialog")).toBeVisible();
  });

  it("hides discard when ownership cannot be proven", () => {
    render(
      <StagedInputRecovery
        jobId="job-1"
        staging={{ ...staging, discard_available: false }}
        onDiscard={() => {}}
      />,
    );
    expect(screen.queryByRole("button", { name: "Discard staged input" })).not.toBeInTheDocument();
  });
});
