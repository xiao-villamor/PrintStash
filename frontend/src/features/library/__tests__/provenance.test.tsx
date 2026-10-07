/** Source receipts retire older reads; no command outlives its session or mounted editor. */
import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useState } from "react";
import { provenanceOptions, useSourceEditing } from "../provenance";
import { clearLogin } from "@/lib/auth-store";
import { aModel, aModelProvenance } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

function Probe() {
  const owner = useSourceEditing(1);
  const [saved, setSaved] = useState(false);
  if (!owner.active) return <p>Retired</p>;
  return (
    <>
      <p>{owner.data?.edit_version}</p>
      <p>{owner.data?.sources[0]?.fields[0]?.effective_value}</p>
      <p>{saved ? "Saved" : "Unsaved"}</p>
      <button
        onClick={() => {
          if (owner.data)
            owner.submit(
              {
                kind: "override",
                sourceId: 8,
                payload: { overrides: { title: "My title" }, clear_overrides: [] },
              },
              owner.data,
              () => setSaved(true),
            );
        }}
      >
        Save
      </button>
      <button onClick={() => void owner.review()}>Review</button>
      <p>{owner.state.phase}</p>
    </>
  );
}

describe("useSourceEditing", () => {
  it("publishes a confirmed Source receipt ahead of a late read", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const confirmed = aModelProvenance({ edit_version: 9 });
    Object.assign(confirmed.sources[0].fields[0], {
      effective_value: "My title",
      effective_origin: "user",
      user_value: "My title",
      user_override_set: true,
    });
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: {
        "GET /api/v1/models/1/provenance": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
        "PATCH /api/v1/models/1/provenance/8": json(confirmed),
      },
    });
    await act(async () => {
      void view.client.refetchQueries({ queryKey: provenanceOptions(1).queryKey });
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("9")).toBeInTheDocument();
    await act(async () => held.resolve(json(aModelProvenance())));
    expect(screen.getByText("9")).toBeInTheDocument();
    expect(signal?.aborted).toBe(true);
    expect(screen.getByText("My title")).toBeInTheDocument();
  });

  it("preserves a newer Source receipt against an older acknowledgement", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: {
        "PATCH /api/v1/models/1/provenance/8": () => held.promise,
      },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    act(() => {
      view.client.setQueryData(
        provenanceOptions(1).queryKey,
        aModelProvenance({ edit_version: 12 }),
      );
    });
    await act(async () => held.resolve(json(aModelProvenance({ edit_version: 9 }))));
    expect(screen.getByText("12")).toBeInTheDocument();
  });

  it("suppresses duplicate submissions", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: { "PATCH /api/v1/models/1/provenance/8": () => held.promise },
    });
    await user.dblClick(screen.getByRole("button", { name: "Save" }));
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
    await act(async () => held.resolve(json(aModelProvenance({ edit_version: 9 }))));
    expect(await screen.findByText("Saved")).toBeInTheDocument();
  });

  it.each([
    { label: "session retirement", unmount: false },
    { label: "unmount", unmount: true },
  ])("ignores a Source acknowledgement after $label", async ({ unmount }) => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: { "PATCH /api/v1/models/1/provenance/8": () => held.promise },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    if (unmount) view.unmount();
    else act(() => clearLogin());
    const replacement = aModelProvenance({ edit_version: 25 });
    view.client.setQueryData(provenanceOptions(1).queryKey, replacement);
    await act(async () => held.resolve(json(aModelProvenance({ edit_version: 9 }))));
    expect(view.client.getQueryData(provenanceOptions(1).queryKey)).toEqual(replacement);
    expect(screen.queryByText("Saved")).not.toBeInTheDocument();
  });

  it("retires an old-session Source review", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: {
        "PATCH /api/v1/models/1/provenance/8": json({ detail: "edit_conflict" }, 412),
        "GET /api/v1/models/1/provenance": json(aModelProvenance({ edit_version: 7 })),
        "GET /api/v1/models/1": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("blocked");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(signal).toBeDefined());
    act(() => clearLogin());
    await act(async () => held.resolve(json(aModel({ edit_version: 7 }))));
    expect(screen.getByText("Retired")).toBeInTheDocument();
    expect(signal?.aborted).toBe(true);
    expect(view.client.getQueryData(provenanceOptions(1).queryKey)).toBeUndefined();
  });
  it("refreshes Source on return after a departed editor writes", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: { "PATCH /api/v1/models/1/provenance/8": () => held.promise },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    view.unmount();
    await act(async () => held.resolve(json(aModelProvenance({ edit_version: 9 }))));
    let reads = 0;
    const next = await view.client.fetchQuery({
      ...provenanceOptions(1, async () => {
        reads++;
        return aModelProvenance({ edit_version: 9 });
      }),
      staleTime: Infinity,
    });
    expect(reads).toBe(1);
    expect(next.edit_version).toBe(9);
  });
  it("cancels the other review read after failure", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Probe />, {
      seed: [[provenanceOptions(1).queryKey, aModelProvenance()]],
      routes: {
        "PATCH /api/v1/models/1/provenance/8": json({ detail: "edit_conflict" }, 412),
        "GET /api/v1/models/1": json({ detail: "review_failed" }, 503),
        "GET /api/v1/models/1/provenance": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("blocked");
    await user.click(screen.getByRole("button", { name: "Review" }));
    await waitFor(() => expect(signal?.aborted).toBe(true));
    await act(async () => held.resolve(json(aModelProvenance({ edit_version: 7 }))));
    expect(screen.getByText("blocked")).toBeInTheDocument();
  });
});
