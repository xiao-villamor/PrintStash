/** Batch undo belongs to the original selection and its confirmed server versions. */
import { act } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { clearLogin } from "@/lib/auth-store";
import { anEditingBase, aModelListItem } from "@/test-support/factories";
import { json, renderApp } from "@/test-support/render";
import { moveLibraryModels, tagLibraryModels } from "../batch-edits";

const accepted = {
  succeeded_ids: [1],
  succeeded_count: 1,
  succeeded_versions: { 1: anEditingBase({ edit_version: 9 }) },
  failed: [],
  failed_count: 0,
};

describe("conditional Library batch edits", () => {
  it("undoes a move against its acknowledged versions", async () => {
    const app = renderApp(<div />, {
      routes: { "POST /api/v1/models/batch/move": json(accepted) },
    });
    const receipt = await moveLibraryModels(
      [aModelListItem({ id: 1, collection: "original", edit_version: 3 })],
      "destination",
    );
    app.route({
      "POST /api/v1/models/batch/move": json({
        ...accepted,
        succeeded_versions: { 1: anEditingBase({ edit_version: 10 }) },
      }),
    });
    await receipt.undo();
    const bodies = app.requestsWithMethod("POST").map((request) => JSON.parse(request.body));
    expect(bodies).toEqual([
      {
        model_ids: [1],
        collection: "destination",
        expected_versions: { 1: anEditingBase({ edit_version: 3 }) },
      },
      {
        model_ids: [1],
        collection: "original",
        expected_versions: { 1: anEditingBase({ edit_version: 9 }) },
      },
    ]);
  });

  it("undoes tags against the acknowledged version", async () => {
    let version: string | null = null;
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/tags": json(accepted),
        "PATCH /api/v1/models/1": (_url, init) => {
          version = new Headers(init?.headers).get("If-Match");
          return json({ id: 1, ...anEditingBase({ edit_version: 12 }) });
        },
      },
    });
    const receipt = await tagLibraryModels(
      [aModelListItem({ id: 1, tags: ["original"] })],
      ["new"],
      [],
    );
    await receipt.undo();
    expect(version).toBe('"model-1-eaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa-v9"');
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({ tags: ["original"] });
  });

  it("retains a concurrent change when undo conflicts", async () => {
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/tags": json(accepted),
        "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
      },
    });
    const receipt = await tagLibraryModels([aModelListItem({ id: 1 })], ["new"], []);
    const result = await receipt.undo();
    expect(result.result.failed).toEqual([{ model_id: 1, reason: "edit_conflict" }]);
    expect(result.result.succeeded_count).toBe(0);
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });

  it("excludes failed rows from undo", async () => {
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/move": json({
          ...accepted,
          failed: [{ model_id: 2, reason: "edit_conflict" }],
          failed_count: 1,
        }),
      },
    });
    const receipt = await moveLibraryModels(
      [aModelListItem({ id: 1 }), aModelListItem({ id: 2 })],
      "destination",
    );
    app.route({
      "POST /api/v1/models/batch/move": json({
        ...accepted,
        succeeded_versions: { 1: anEditingBase({ edit_version: 10 }) },
      }),
    });
    await receipt.undo();
    expect(JSON.parse(app.requestsWithMethod("POST")[1].body).model_ids).toEqual([1]);
  });

  it("stops remaining chunks after session retirement", async () => {
    let answer!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      answer = resolve;
    });
    const app = renderApp(<div />, { routes: { "POST /api/v1/models/batch/move": () => pending } });
    const models = Array.from({ length: 501 }, (_, index) => aModelListItem({ id: index + 1 }));
    const move = moveLibraryModels(models, "destination");
    const rejected = move.catch((error: Error) => error);
    act(() => clearLogin());
    answer(json(accepted));
    expect(await rejected).toMatchObject({
      name: "AbortError",
      message: "request_session_changed",
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("refuses an undo from a retired session", async () => {
    const app = renderApp(<div />, {
      routes: { "POST /api/v1/models/batch/move": json(accepted) },
    });
    const receipt = await moveLibraryModels([aModelListItem({ id: 1 })], "destination");
    act(() => clearLogin());
    await expect(receipt.undo()).rejects.toThrow("request_session_changed");
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("preserves partial results across the batch boundary", async () => {
    const app = renderApp(<div />, {
      routes: { "POST /api/v1/models/batch/move": json(accepted) },
    });
    const models = Array.from({ length: 501 }, (_, index) => aModelListItem({ id: index + 1 }));
    app.route({
      "POST /api/v1/models/batch/move": (_url, init) => {
        const ids: number[] = JSON.parse(String(init?.body)).model_ids;
        return json({
          succeeded_ids: ids,
          succeeded_count: ids.length,
          succeeded_versions: Object.fromEntries(
            ids.map((id) => [id, anEditingBase({ edit_version: 9 })]),
          ),
          failed: [],
          failed_count: 0,
        });
      },
    });
    const receipt = await moveLibraryModels(models, "destination");
    expect(
      app.requestsWithMethod("POST").map((request) => JSON.parse(request.body).model_ids.length),
    ).toEqual([500, 1]);
    expect(receipt.result.succeeded_ids).toEqual(models.map((model) => model.id));
    expect(receipt.result.succeeded_versions[501]).toEqual(anEditingBase({ edit_version: 9 }));
  });
  it.each([
    {
      label: "move",
      path: "move",
      run: (models: ReturnType<typeof aModelListItem>[]) =>
        moveLibraryModels(models, "destination"),
    },
    {
      label: "tags",
      path: "tags",
      run: (models: ReturnType<typeof aModelListItem>[]) => tagLibraryModels(models, ["new"], []),
    },
  ])(
    "preserves confirmed receipts when a later $label request is unconfirmed",
    async ({ path, run }) => {
      const models = Array.from({ length: 1001 }, (_, index) => aModelListItem({ id: index + 1 }));
      const ids = models.slice(0, 500).map((model) => model.id);
      let calls = 0;
      const app = renderApp(<div />, {
        routes: {
          [`POST /api/v1/models/batch/${path}`]: () =>
            ++calls === 1
              ? json({
                  succeeded_ids: ids,
                  succeeded_count: 500,
                  succeeded_versions: Object.fromEntries(
                    ids.map((id) => [id, anEditingBase({ edit_version: 9 })]),
                  ),
                  failed: [],
                  failed_count: 0,
                })
              : json({ detail: "unavailable" }, 503),
        },
      });

      const receipt = await run(models);

      expect(receipt.result.succeeded_ids).toEqual(ids);
      expect(receipt.result.succeeded_versions[500]).toEqual(anEditingBase({ edit_version: 9 }));
      expect(receipt.completion).toMatchObject({
        status: "interrupted",
        unconfirmedIds: models.slice(500, 1000).map((model) => model.id),
        unattemptedIds: [1001],
      });
      expect(app.requestsWithMethod("POST")).toHaveLength(2);
    },
  );

  it("undoes only confirmed rows from an interrupted batch", async () => {
    const models = Array.from({ length: 501 }, (_, index) =>
      aModelListItem({ id: index + 1, collection: "original" }),
    );
    let calls = 0;
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/move": () =>
          ++calls === 1
            ? json({
                ...accepted,
                failed: models
                  .slice(1, 500)
                  .map((model) => ({ model_id: model.id, reason: "edit_conflict" })),
                failed_count: 499,
              })
            : json({ detail: "unavailable" }, 503),
      },
    });
    const receipt = await moveLibraryModels(models, "destination");
    app.route({
      "POST /api/v1/models/batch/move": json({
        ...accepted,
        succeeded_versions: { 1: anEditingBase({ edit_version: 12 }) },
      }),
    });

    const undone = await receipt.undo();

    expect(undone.result.succeeded_ids).toEqual([1]);
    expect(JSON.parse(app.requestsWithMethod("POST")[2].body)).toEqual({
      model_ids: [1],
      collection: "original",
      expected_versions: { 1: anEditingBase({ edit_version: 9 }) },
    });
  });

  it("preserves confirmed undo progress after interruption", async () => {
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/tags": json({
          ...accepted,
          succeeded_ids: [1, 2, 3],
          succeeded_count: 3,
          succeeded_versions: {
            1: anEditingBase({ edit_version: 9 }),
            2: anEditingBase({ edit_version: 9 }),
            3: anEditingBase({ edit_version: 9 }),
          },
        }),
        "PATCH /api/v1/models/1": json({ id: 1, ...anEditingBase({ edit_version: 12 }) }),
        "PATCH /api/v1/models/2": json({ detail: "unavailable" }, 503),
      },
    });
    const receipt = await tagLibraryModels(
      [aModelListItem({ id: 1 }), aModelListItem({ id: 2 }), aModelListItem({ id: 3 })],
      ["new"],
      [],
    );

    const undone = await receipt.undo();

    expect(undone.result.succeeded_ids).toEqual([1]);
    expect(undone.result.succeeded_versions[1]).toEqual(anEditingBase({ edit_version: 12 }));
    expect(undone.completion).toMatchObject({
      status: "interrupted",
      unconfirmedIds: [2],
      unattemptedIds: [3],
    });
    expect(app.requestsWithMethod("PATCH").map((request) => request.url)).toEqual([
      "/api/v1/models/1",
      "/api/v1/models/2",
    ]);
  });

  it.each([
    { label: "duplicate", response: { ...accepted, succeeded_ids: [1, 1], succeeded_count: 2 } },
    {
      label: "missing",
      response: { ...accepted, succeeded_ids: [], succeeded_versions: {}, succeeded_count: 0 },
    },
    {
      label: "foreign",
      response: {
        ...accepted,
        succeeded_ids: [2],
        succeeded_versions: { 2: anEditingBase({ edit_version: 9 }) },
      },
    },
    {
      label: "nonadvancing",
      response: { ...accepted, succeeded_versions: { 1: anEditingBase({ edit_version: 1 }) } },
    },
  ])("rejects $label batch acknowledgements without inventing successes", async ({ response }) => {
    const app = renderApp(<div />, {
      routes: { "POST /api/v1/models/batch/move": json(response) },
    });

    const receipt = await moveLibraryModels([aModelListItem({ id: 1 })], "destination");

    expect(receipt.result.succeeded_ids).toEqual([]);
    expect(receipt.completion).toMatchObject({
      status: "interrupted",
      unconfirmedIds: [1],
      unattemptedIds: [],
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });

  it("suppresses interrupted batch feedback after session retirement", async () => {
    const response = Promise.withResolvers<Response>();
    const app = renderApp(<div />, {
      routes: { "POST /api/v1/models/batch/move": () => response.promise },
    });
    const pending = moveLibraryModels([aModelListItem({ id: 1 })], "destination");
    const rejected = pending.catch((error: Error) => error);

    act(() => clearLogin());
    response.resolve(json({ detail: "unavailable" }, 503));

    expect(await rejected).toMatchObject({
      name: "AbortError",
      message: "request_session_changed",
    });
    expect(app.requestsWithMethod("POST")).toHaveLength(1);
  });
  it("stops later destination groups after an interrupted move undo", async () => {
    const app = renderApp(<div />, {
      routes: {
        "POST /api/v1/models/batch/move": json({
          ...accepted,
          succeeded_ids: [1, 2, 3],
          succeeded_count: 3,
          succeeded_versions: {
            1: anEditingBase({ edit_version: 9 }),
            2: anEditingBase({ edit_version: 9 }),
            3: anEditingBase({ edit_version: 9 }),
          },
        }),
      },
    });
    const receipt = await moveLibraryModels(
      [
        aModelListItem({ id: 1, collection: "first" }),
        aModelListItem({ id: 2, collection: "second" }),
        aModelListItem({ id: 3, collection: "third" }),
      ],
      "destination",
    );
    let calls = 0;
    app.route({
      "POST /api/v1/models/batch/move": () =>
        ++calls === 1
          ? json({ ...accepted, succeeded_versions: { 1: anEditingBase({ edit_version: 12 }) } })
          : json({ detail: "unavailable" }, 503),
    });

    const undone = await receipt.undo();

    expect(undone.result.succeeded_ids).toEqual([1]);
    expect(undone.completion).toMatchObject({
      status: "interrupted",
      unconfirmedIds: [2],
      unattemptedIds: [3],
    });
    expect(
      app.requestsWithMethod("POST").map((request) => JSON.parse(request.body).collection),
    ).toEqual(["destination", "first", "second"]);
  });
});
