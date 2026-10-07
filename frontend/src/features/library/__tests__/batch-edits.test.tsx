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
    expect(result.failed).toEqual([{ model_id: 1, reason: "edit_conflict" }]);
    expect(result.succeeded_count).toBe(0);
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
});
