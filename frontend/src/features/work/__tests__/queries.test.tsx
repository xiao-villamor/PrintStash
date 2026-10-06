/**
 * Work feature ownership: administrative read snapshots, acknowledged mutations,
 * and exact reconciliation. Session and view retirement must discard late results
 * without turning a permission failure into a successful cached update.
 */
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";
import { useWorkMutation, workKeys, type WorkChange } from "@/features/work/queries";
import { queryClient, queryKeys } from "@/lib/query-client";
import { getUser, retirePrivateSessionScope } from "@/lib/auth-store";
import { ApiError } from "@/lib/errors";
import { aJob, aWorkOverview } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import type { WorkOverview } from "@/types";

function MutationProbe({ change }: { change: WorkChange }) {
  const mutation = useWorkMutation();
  return (
    <>
      <button onClick={() => mutation.mutate(change)}>Apply</button>
      <output>
        {mutation.isSuccess
          ? "saved"
          : mutation.error instanceof ApiError
            ? `failure:${mutation.error.status}`
            : mutation.error?.name}
      </output>
    </>
  );
}

beforeEach(() => {
  queryClient.clear();
});

const cases: Array<{ label: string; change: WorkChange; route: string }> = [
  {
    label: "lane",
    change: { kind: "lane", lane: "derive.native", concurrency: 4 },
    route: "PUT /api/v1/admin/work/lanes/derive.native",
  },
  {
    label: "cancel",
    change: { kind: "cancel-job", jobId: "job" },
    route: "POST /api/v1/jobs/job/cancel",
  },
  { label: "retry", change: { kind: "retry", jobId: "job" }, route: "POST /api/v1/jobs/job/retry" },
  {
    label: "queue",
    change: { kind: "cancel-queued", definition: "derivatives.mesh" },
    route: "POST /api/v1/admin/work/cancel-queued",
  },
  {
    label: "policy",
    change: { kind: "policy", body: { derivatives_mesh_enabled: false } },
    route: "PUT /api/v1/config",
  },
];

describe("work mutation owner", () => {
  it.each(cases)("preserves current unauthorized $label failures", async ({ change, route }) => {
    const user = userEvent.setup();
    renderApp(<MutationProbe change={change} />, {
      routes: { [route]: json({ detail: "session_expired" }, 401) },
    });
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("failure:401")).toBeVisible();
    expect(getUser()).toBeNull();
  });

  it.each(cases)("keeps work data after denied $label writes", async ({ change, route }) => {
    const user = userEvent.setup();
    renderApp(<MutationProbe change={change} />, {
      routes: { [route]: json({ detail: "forbidden" }, 403) },
    });
    const previous = aWorkOverview();
    queryClient.setQueryData(workKeys.overview, previous);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("failure:403")).toBeVisible();
    expect(queryClient.getQueryData(workKeys.overview)).toEqual(previous);
    expect(queryClient.getQueryState(workKeys.overview)?.isInvalidated).toBe(false);
  });

  it("publishes acknowledged work changes after cancelling old reads", async () => {
    const user = userEvent.setup();
    const saved = aWorkOverview();
    saved.lanes = saved.lanes.map((lane) => ({ ...lane, concurrency: 4 }));
    renderApp(<MutationProbe change={{ kind: "lane", lane: "derive.native", concurrency: 4 }} />, {
      routes: { "PUT /api/v1/admin/work/lanes/derive.native": json(saved) },
    });
    queryClient.setQueryData(workKeys.overview, aWorkOverview());
    let finish: (overview: WorkOverview) => void = () => {};
    let signal: AbortSignal | undefined;
    const old = queryClient
      .fetchQuery({
        queryKey: workKeys.overview,
        queryFn: (request) => {
          signal = request.signal;
          return new Promise<WorkOverview>((resolve) => {
            finish = resolve;
          });
        },
        staleTime: 0,
      })
      .catch((error: Error) => error);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await screen.findByText("saved");
    expect(signal?.aborted).toBe(true);
    finish(aWorkOverview());
    await old;
    expect(queryClient.getQueryData<WorkOverview>(workKeys.overview)?.lanes[0].concurrency).toBe(4);
  });

  it.each(cases)(
    "invalidates affected work resources for $label",
    async ({ change, route, label }) => {
      const user = userEvent.setup();
      const saved = aWorkOverview();
      saved.lanes = saved.lanes.map((lane) => ({ ...lane, concurrency: 4 }));
      const acknowledgement =
        change.kind === "lane"
          ? json(saved)
          : change.kind === "cancel-queued"
            ? json({ cancelled: 2 })
            : change.kind === "policy"
              ? json({ derivatives_mesh_enabled: false })
              : json(
                  aJob({
                    job_id: "job",
                    state: change.kind === "cancel-job" ? "cancelled" : "queued",
                  }),
                );
      renderApp(<MutationProbe change={change} />, { routes: { [route]: acknowledgement } });
      queryClient.setQueryData(workKeys.overview, aWorkOverview());
      queryClient.setQueryData(workKeys.jobs, [aJob({ job_id: "job", state: "running" })]);
      queryClient.setQueryData(queryKeys.vaultConfig, { sentinel: true });
      queryClient.setQueryData(["unrelated"], { sentinel: true });
      await user.click(screen.getByRole("button", { name: "Apply" }));
      await screen.findByText("saved");
      expect(queryClient.getQueryState(workKeys.overview)?.isInvalidated).toBe(true);
      expect(queryClient.getQueryState(workKeys.jobs)?.isInvalidated).toBe(label !== "lane");
      expect(queryClient.getQueryState(queryKeys.vaultConfig)?.isInvalidated).toBe(
        label === "policy",
      );
      expect(queryClient.getQueryState(["unrelated"])?.isInvalidated).toBe(false);
    },
  );

  it("suppresses a late work acknowledgement after retirement", async () => {
    const user = userEvent.setup();
    let finish: (response: Response) => void = () => {};
    renderApp(<MutationProbe change={{ kind: "retry", jobId: "job" }} />, {
      routes: {
        "POST /api/v1/jobs/job/retry": () =>
          new Promise((resolve) => {
            finish = resolve;
          }),
      },
    });
    queryClient.setQueryData(workKeys.jobs, []);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    await act(async () => {
      retirePrivateSessionScope();
      finish(json(aJob({ job_id: "job", state: "queued" })));
    });
    expect(await screen.findByText("AbortError")).toBeVisible();
    expect(queryClient.getQueryData(workKeys.jobs)).toBeUndefined();
  });
});

describe("work acknowledgement identity", () => {
  it("rejects a lane acknowledgement missing its lane", async () => {
    const user = userEvent.setup();
    const wrong = aWorkOverview();
    wrong.lanes = [];
    renderApp(<MutationProbe change={{ kind: "lane", lane: "derive.native", concurrency: 4 }} />, {
      routes: { "PUT /api/v1/admin/work/lanes/derive.native": json(wrong) },
    });
    const previous = aWorkOverview();
    queryClient.setQueryData(workKeys.overview, previous);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("Error")).toBeVisible();
    expect(queryClient.getQueryData(workKeys.overview)).toEqual(previous);
    expect(queryClient.getQueryState(workKeys.overview)?.isInvalidated).toBe(false);
  });
  it("rejects a Job acknowledgement for a different Job", async () => {
    const user = userEvent.setup();
    renderApp(<MutationProbe change={{ kind: "retry", jobId: "job" }} />, {
      routes: { "POST /api/v1/jobs/job/retry": json(aJob({ job_id: "other", state: "queued" })) },
    });
    const previous = [aJob({ job_id: "job", state: "running" })];
    queryClient.setQueryData(workKeys.jobs, previous);
    await user.click(screen.getByRole("button", { name: "Apply" }));
    expect(await screen.findByText("Error")).toBeVisible();
    expect(queryClient.getQueryData(workKeys.jobs)).toEqual(previous);
    expect(queryClient.getQueryState(workKeys.jobs)?.isInvalidated).toBe(false);
  });
});

describe("work command retries", () => {
  it("does not repeat a failed work command", async () => {
    const user = userEvent.setup();
    let commands = 0;
    const view = renderApp(<MutationProbe change={{ kind: "retry", jobId: "job" }} />, {
      routes: {
        "POST /api/v1/jobs/job/retry": () => {
          commands++;
          return json({ detail: "forbidden" }, 403);
        },
      },
    });

    queryClient.setDefaultOptions({
      ...queryClient.getDefaultOptions(),
      mutations: { retry: 1, retryDelay: 0 },
    });
    view.rerender(<MutationProbe change={{ kind: "retry", jobId: "job" }} />);

    await user.click(screen.getByRole("button", { name: "Apply" }));

    expect(await screen.findByText("failure:403")).toBeVisible();
    expect(commands).toBe(1);
  });
});
