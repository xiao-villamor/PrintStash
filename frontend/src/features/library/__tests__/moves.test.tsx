/** Model moves preserve gesture intent through explicit conflict review and retire with their view. */
import "@testing-library/jest-dom/vitest";
import { useMemo, useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useModelMoves } from "../moves";
import { modelDetailOptions } from "../model-detail";
import { ModelMoveReview } from "@/components/model-move-review";
import { clearLogin } from "@/lib/auth-store";
import { captureModelDrag } from "@/lib/model-dnd";
import { getSessionVersion } from "@/lib/session-transport";
import { queryKeys } from "@/lib/query-client";
import { aModel } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";

function Probe({ href = "/" }: { href?: string }) {
  const entry = useMemo(
    () => ({ key: href, href, session: getSessionVersion(), index: 0 }),
    [href],
  );
  const [confirmed, setConfirmed] = useState(0);
  const moves = useModelMoves(entry, async () => setConfirmed((count) => count + 1));
  return (
    <>
      <p>Confirmed: {confirmed}</p>
      <button
        onClick={() =>
          void moves.move(
            captureModelDrag(
              aModel({ id: 1, name: "Original name", edit_version: 7, collection: "source" }),
            ),
            "parts",
          )
        }
      >
        Move
      </button>
      <button
        onClick={() =>
          void moves.move(captureModelDrag(aModel({ id: 1, edit_version: 8 })), "wrong destination")
        }
      >
        Duplicate
      </button>
      <button
        onClick={() =>
          void moves.move(captureModelDrag(aModel({ id: 2, edit_version: 3 })), "other")
        }
      >
        Other
      </button>
      <ModelMoveReview moves={moves} />
    </>
  );
}
const conflict = () => json({ detail: "edit_conflict" }, 412);
const latest = () =>
  json(
    aModel({
      id: 1,
      name: "Latest name",
      edit_version: 10,
      collection: "elsewhere",
      effective_role: "edit",
    }),
  );
const retryButton = () =>
  screen.getByRole("button", { name: "Save my draft against this version" });

describe("useModelMoves", () => {
  it("preserves the requested destination after a conflict without reading automatically", async () => {
    const user = userEvent.setup();
    const view = renderApp(<Probe />, { routes: { "PATCH /api/v1/models/1": conflict() } });
    await user.click(screen.getByRole("button", { name: "Move" }));
    expect(await screen.findByText("Move Original name to parts.")).toBeVisible();
    expect(view.requests().filter((request) => request.method === "GET")).toHaveLength(0);
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Save my draft against this version" })).toBeNull();
  });

  it.each(["network", "503", "invalid ACK"])(
    "requires explicit review after %s",
    async (failure) => {
      const user = userEvent.setup();
      renderApp(<Probe />, {
        routes: {
          "PATCH /api/v1/models/1": () => {
            if (failure === "network") throw new TypeError("Failed to fetch");
            return failure === "503"
              ? json({ detail: "unavailable" }, 503)
              : json(aModel({ edit_version: 7 }));
          },
        },
      });
      await user.click(screen.getByRole("button", { name: "Move" }));
      expect(
        await screen.findByText(
          "The save was not confirmed. Review the latest version before retrying.",
        ),
      ).toBeVisible();
      expect(screen.getByText("Move Original name to parts.")).toBeVisible();
      expect(screen.getByText("Confirmed: 0")).toBeVisible();
    },
  );

  it("retries the original destination using exactly the reviewed version", async () => {
    const user = userEvent.setup();
    const versions: (string | null)[] = [];
    const view = renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": (_url, init) => {
          versions.push(new Headers(init?.headers).get("If-Match"));
          return versions.length === 1
            ? conflict()
            : json(aModel({ id: 1, edit_version: 11, collection: "parts" }));
        },
        "GET /api/v1/models/1": latest(),
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("Current location: elsewhere")).toBeVisible();
    await user.click(retryButton());
    await screen.findByText("Confirmed: 1");
    expect(versions).toEqual(['"model-1-v7"', '"model-1-v10"']);
    expect(view.requestsWithMethod("PATCH").map((request) => JSON.parse(request.body))).toEqual([
      { collection: "parts" },
      { collection: "parts" },
    ]);
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      edit_version: 11,
      collection: "parts",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("adopts the reviewed location without another write", async () => {
    const user = userEvent.setup();
    const view = renderApp(<Probe />, {
      routes: { "PATCH /api/v1/models/1": conflict(), "GET /api/v1/models/1": latest() },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(await screen.findByRole("button", { name: "Use latest version" }));
    await screen.findByText("Confirmed: 1");
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      edit_version: 10,
      collection: "elsewhere",
    });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps the intended destination when review fails", async () => {
    const user = userEvent.setup();
    renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": conflict(),
        "GET /api/v1/models/1": json({ detail: "unavailable" }, 503),
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByText(
        "Could not load the current model. Review it again before retrying the move.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Move Original name to parts.")).toBeVisible();
    expect(screen.queryByRole("button", { name: "Save my draft against this version" })).toBeNull();
  });

  it.each([403, 404])("hides private move details after review returns %s", async (status) => {
    const user = userEvent.setup();
    renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": conflict(),
        "GET /api/v1/models/1": json({ detail: "denied" }, status),
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("This model is no longer available to you.")).toBeVisible();
    expect(screen.queryByText("Move Original name to parts.")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save my draft against this version" })).toBeNull();
  });

  it("disables retry when the reviewed role cannot edit", async () => {
    const user = userEvent.setup();
    renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": conflict(),
        "GET /api/v1/models/1": json(aModel({ edit_version: 10, effective_role: "view" })),
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    ).toBeDisabled();
  });

  it("requires a fresh review after a second conflict", async () => {
    const user = userEvent.setup();
    const view = renderApp(<Probe />, {
      routes: { "PATCH /api/v1/models/1": conflict, "GET /api/v1/models/1": latest },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).toBeNull(),
    );
    expect(view.requestsWithMethod("PATCH")).toHaveLength(2);
    expect(view.requests().filter((request) => request.method === "GET")).toHaveLength(1);
    expect(screen.getByText("Move Original name to parts.")).toBeVisible();
  });

  it("suppresses duplicate moves without replacing the first destination", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, { routes: { "PATCH /api/v1/models/1": () => held.promise } });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(screen.getByRole("button", { name: "Duplicate" }));
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body)).toEqual({ collection: "parts" });
    await act(async () => held.resolve(conflict()));
    expect(await screen.findByText("Move Original name to parts.")).toBeVisible();
  });

  it("confirms unrelated Model moves while the first is pending", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const other = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": () => held.promise,
        "PATCH /api/v1/models/2": () => other.promise,
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(screen.getByRole("button", { name: "Other" }));
    expect(view.requestsWithMethod("PATCH")).toHaveLength(2);
    await act(async () =>
      other.resolve(json(aModel({ id: 2, edit_version: 4, collection: "other" }))),
    );
    expect(await screen.findByText("Confirmed: 1")).toBeVisible();
    expect(view.client.getQueryData(queryKeys.model(2))).toMatchObject({ collection: "other" });
    await act(async () => held.resolve(conflict()));
    expect(await screen.findByText("Move Original name to parts.")).toBeVisible();
  });

  it.each(["session", "navigation", "unmount"])(
    "ignores a late receipt after %s retirement",
    async (retirement) => {
      const user = userEvent.setup();
      const held = Promise.withResolvers<Response>();
      const view = renderApp(<Probe />, {
        routes: { "PATCH /api/v1/models/1": () => held.promise },
      });
      await user.click(screen.getByRole("button", { name: "Move" }));
      if (retirement === "session") act(() => clearLogin());
      else if (retirement === "navigation") view.rerender(<Probe href="/other" />);
      else view.unmount();
      await act(async () =>
        held.resolve(json(aModel({ id: 1, edit_version: 8, collection: "parts" }))),
      );
      expect(view.client.getQueryData(queryKeys.model(1))).toBeUndefined();
      expect(screen.queryByText("Confirmed: 1")).toBeNull();
      expect(screen.queryByText("Moved")).toBeNull();
      expect(screen.queryByRole("dialog")).toBeNull();
    },
  );

  it("aborts a dismissed review without reopening it", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    let signal: AbortSignal | null | undefined;
    renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": conflict(),
        "GET /api/v1/models/1": (_url, init) => {
          signal = init?.signal;
          return held.promise;
        },
      },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(signal?.aborted).toBe(true);
    await act(async () => held.resolve(latest()));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("preserves a confirmed move when an older detail read completes late", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<Probe />, {
      routes: {
        "PATCH /api/v1/models/1": json(aModel({ id: 1, edit_version: 8, collection: "parts" })),
        "GET /api/v1/models/1": () => held.promise,
      },
    });
    const read = view.client.fetchQuery(modelDetailOptions(1)).catch(() => undefined);
    await waitFor(() =>
      expect(view.requests().some((request) => request.method === "GET")).toBe(true),
    );
    await user.click(screen.getByRole("button", { name: "Move" }));
    await screen.findByText("Confirmed: 1");
    await act(async () => {
      held.resolve(json(aModel({ id: 1, edit_version: 7, collection: "source" })));
      await read;
    });
    expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject({
      edit_version: 8,
      collection: "parts",
    });
  });

  it.each([403, 422])("does not publish a rejected write with HTTP %s", async (status) => {
    const user = userEvent.setup();
    const view = renderApp(<Probe />, {
      routes: { "PATCH /api/v1/models/1": json({ detail: "denied" }, status) },
    });
    await user.click(screen.getByRole("button", { name: "Move" }));
    expect(
      await screen.findByText(
        "Something went wrong reaching the server. Check that PrintStash is running and try again.",
      ),
    ).toBeVisible();
    expect(screen.getByText("Confirmed: 0")).toBeVisible();
    expect(view.client.getQueryData(queryKeys.model(1))).toBeUndefined();
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
  });
});
