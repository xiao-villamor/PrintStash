/** Taxonomy changes update shared choices without transport cache policy. */
import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { useTaxonomyCommands } from "../taxonomy";
import { useTags } from "@/lib/queries";
import { queryKeys } from "@/lib/query-client";
import { clearLogin } from "@/lib/auth-store";
import { json, renderApp } from "@/test-support/render";
import { aTag, aCollection } from "@/test-support/factories";

function Choices() {
  const tags = useTags();
  return <p>{tags.data?.map((tag) => tag.name).join(",")}</p>;
}
function Editor({ remove = false }: { remove?: boolean }) {
  const commands = useTaxonomyCommands();
  const [result, setResult] = useState("idle");
  return (
    <>
      <Choices />
      <Choices />
      <p>{result}</p>
      <button
        onClick={() => {
          void (remove ? commands.deleteTag(7) : commands.createTag({ name: "New" })).then(
            () => setResult("saved"),
            () => setResult("failed"),
          );
        }}
      >
        Save
      </button>
    </>
  );
}
describe("taxonomy commands", () => {
  it("publishes a created tag to shared choices", async () => {
    let created = false;
    const tag = aTag({ id: 7, name: "New" });
    renderApp(<Editor />, {
      routes: {
        "GET /api/v1/tags": () => json(created ? [tag] : []),
        "POST /api/v1/tags": () => {
          created = true;
          return json(tag);
        },
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findAllByText("New")).toHaveLength(2);
  });
  it("removes a confirmed tag from shared choices", async () => {
    let deleted = false;
    const app = renderApp(<Editor remove />, {
      seed: [[queryKeys.models, []]],
      routes: {
        "GET /api/v1/tags": () => json(deleted ? [] : [aTag({ id: 7, name: "Old" })]),
        "DELETE /api/v1/tags/7": () => {
          deleted = true;
          return json(null, 204);
        },
      },
    });
    await screen.findAllByText("Old");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("saved");
    await waitFor(() => expect(screen.queryAllByText("Old")).toHaveLength(0));
    expect(app.client.getQueryState(queryKeys.models)?.isInvalidated).toBe(true);
  });
  it("preserves taxonomy after a failed command", async () => {
    renderApp(<Editor />, {
      routes: {
        "GET /api/v1/tags": json([aTag({ name: "Old" })]),
        "POST /api/v1/tags": json({ detail: "denied" }, 403),
      },
    });
    await screen.findAllByText("Old");
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("failed");
    expect(screen.getAllByText("Old")).toHaveLength(2);
    expect(screen.queryByText("New")).toBeNull();
  });
  it("fences taxonomy publication after session replacement", async () => {
    const held = Promise.withResolvers<Response>();
    const app = renderApp(<Editor />, {
      routes: { "GET /api/v1/tags": json([]), "POST /api/v1/tags": () => held.promise },
    });
    await userEvent.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(app.requestsWithMethod("POST")).toHaveLength(1));
    act(() => clearLogin());
    app.client.setQueryData(queryKeys.tags, [aTag({ name: "Replacement" })]);
    await act(async () => held.resolve(json(aTag({ name: "New" }))));
    expect(app.client.getQueryState(queryKeys.tags)?.isInvalidated).toBe(false);
    expect(app.client.getQueryData(queryKeys.tags)).toEqual([aTag({ name: "Replacement" })]);
  });
  it.each(["create", "move", "rename", "delete", "tags", "readme"] as const)(
    "revalidates collection projections after %s",
    async (kind) => {
      function CollectionEditor() {
        const commands = useTaxonomyCommands();
        const [done, setDone] = useState(false);
        return (
          <button
            onClick={() => {
              const work =
                kind === "create"
                  ? commands.createCollection({ name: "New" })
                  : kind === "move"
                    ? commands.moveCollection(7, null)
                    : kind === "rename"
                      ? commands.renameCollection(7, "New")
                      : kind === "delete"
                        ? commands.deleteCollection(7)
                        : kind === "tags"
                          ? commands.replaceCollectionTags(7, ["New"])
                          : commands.setCollectionReadme(7, "New");
              void work.then(() => setDone(true));
            }}
          >
            {done ? "saved" : "Save"}
          </button>
        );
      }
      const keys = [
        queryKeys.collections,
        queryKeys.models,
        queryKeys.multipartModels,
        queryKeys.vaultStats,
      ];
      const app = renderApp(<CollectionEditor />, {
        seed: keys.map((key) => [key, []]),
        routes: {
          "POST /api/v1/collections": json(aCollection({ id: 7 })),
          "PATCH /api/v1/collections/7": json(aCollection({ id: 7 })),
          "DELETE /api/v1/collections/7": json(null, 204),
          "PUT /api/v1/collections/7/tags": json(aCollection({ id: 7 })),
          "PUT /api/v1/collections/7/readme": json({ readme: "New" }),
        },
      });
      await userEvent.click(screen.getByRole("button", { name: "Save" }));
      await screen.findByText("saved");
      for (const key of keys) expect(app.client.getQueryState(key)?.isInvalidated).toBe(true);
    },
  );
});
