/** Cover intents keep their editing base, survive uncertain saves, and need explicit review. */
import "@testing-library/jest-dom/vitest";
import { useState } from "react";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { ModelCoverEditor } from "../cover-editor";
import { aModel, aModelProvenance } from "@/test-support/factories";
import { renderApp, json, type RouteTable } from "@/test-support/render";
import { clearLogin } from "@/lib/auth-store";
import type { ModelRead } from "@/types";

function Editor({
  initial = aModel(),
  capture = null,
}: {
  initial?: ModelRead;
  capture?: (() => Promise<Blob>) | null;
}) {
  const [model, setModel] = useState(initial);
  const [open, setOpen] = useState(true);
  return (
    <>
      <output>{model.has_custom_cover ? "Saved custom cover" : "Saved automatic cover"}</output>
      {open && (
        <ModelCoverEditor
          model={model}
          capture={capture}
          onClose={() => setOpen(false)}
          publish={async (next) => {
            setModel(next);
            return true;
          }}
        />
      )}
    </>
  );
}

function routes(extra: RouteTable = {}): RouteTable {
  return {
    "GET /api/v1/models/1/provenance": json(aModelProvenance({ sources: [] })),
    ...extra,
  };
}

describe("Model cover editor", () => {
  it("uploads an image with the original editing base", async () => {
    const model = aModel();
    let sent: RequestInit | undefined;
    renderApp(<Editor initial={model} />, {
      routes: routes({
        "PUT /api/v1/models/1/cover": (_url, init) => {
          sent = init;
          return json(aModel({ has_custom_cover: true, edit_version: 2 }));
        },
      }),
    });
    await userEvent.upload(
      screen.getByLabelText("Upload image"),
      new File(["png"], "chosen.png", { type: "image/png" }),
    );
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByText("Saved custom cover")).toBeVisible();
    expect(new Headers(sent?.headers).get("If-Match")).toBe(`"model-1-e${model.edit_epoch}-v1"`);
    const body = sent?.body;
    if (!(body instanceof FormData)) throw new Error("Expected a multipart image upload");
    expect(body.get("file")).toMatchObject({
      name: "chosen.png",
      type: "image/png",
    });
  });

  it("uploads the captured 3D view", async () => {
    let sent: RequestInit | undefined;
    renderApp(<Editor capture={async () => new Blob(["pixels"], { type: "image/png" })} />, {
      routes: routes({
        "PUT /api/v1/models/1/cover": (_url, init) => {
          sent = init;
          return json(aModel({ has_custom_cover: true, edit_version: 2 }));
        },
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Use current 3D view" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const body = sent?.body;
    if (!(body instanceof FormData)) throw new Error("Expected a multipart image upload");
    expect(body.get("file")).toMatchObject({ name: "model-view.png", size: 6 });
  });

  it("chooses an available source image", async () => {
    const provenance = aModelProvenance();
    provenance.sources[0].cover = {
      id: 4,
      provenance_source_id: 8,
      content_type: "image/webp",
      size_bytes: 100,
      updated_at: "2026-01-01T00:00:00Z",
    };
    const app = renderApp(<Editor />, {
      routes: routes({
        "GET /api/v1/models/1/provenance": json(provenance),
        "POST /api/v1/models/1/cover/source": json(
          aModel({ has_custom_cover: true, edit_version: 2 }),
        ),
      }),
    });
    await userEvent.click(await screen.findByRole("button", { name: "Use image from printables" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(app.requestsWithMethod("POST")[0].body).toBe(JSON.stringify({ source_id: 8 }));
    expect(screen.getByText("Saved custom cover")).toBeVisible();
  });

  it("resets a custom cover to automatic", async () => {
    const app = renderApp(<Editor initial={aModel({ has_custom_cover: true })} />, {
      routes: routes({
        "DELETE /api/v1/models/1/cover": json(aModel({ edit_version: 2 })),
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(app.requestsWithMethod("DELETE")).toHaveLength(1);
    expect(screen.getByText("Saved automatic cover")).toBeVisible();
  });

  it("disables actions whose inputs are unavailable", async () => {
    renderApp(<Editor />, { routes: routes() });
    expect(screen.getByRole("button", { name: "Use current 3D view" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Reset to automatic" })).toBeDisabled();
    expect(await screen.findByText("No source images available.")).toBeVisible();
  });

  it("rejects an unsupported image before sending it", async () => {
    const app = renderApp(<Editor />, { routes: routes() });
    await userEvent
      .setup({ applyAccept: false })
      .upload(
        screen.getByLabelText("Upload image"),
        new File(["svg"], "unsafe.svg", { type: "image/svg+xml" }),
      );
    expect(await screen.findByRole("alert")).toHaveTextContent("Choose a PNG, JPEG, or WebP");
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it("shows a failed capture without changing the cover", async () => {
    const app = renderApp(
      <Editor
        capture={async () => {
          throw new Error("Canvas unavailable");
        }}
      />,
      { routes: routes() },
    );
    await userEvent.click(screen.getByRole("button", { name: "Use current 3D view" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be captured");
    expect(app.requestsWithMethod("PUT")).toHaveLength(0);
  });

  it.each(["conflict", "server failure", "invalid receipt"] as const)(
    "requires explicit review after %s",
    async (failure) => {
      let writes = 0;
      const matches: (string | null)[] = [];
      renderApp(<Editor initial={aModel({ has_custom_cover: true })} />, {
        routes: routes({
          "DELETE /api/v1/models/1/cover": (_url, init) => {
            writes++;
            matches.push(new Headers(init?.headers).get("If-Match"));
            if (writes === 1)
              return failure === "invalid receipt"
                ? json(aModel())
                : json({ detail: "edit_conflict" }, failure === "conflict" ? 412 : 503);
            return json(aModel({ edit_version: 4 }));
          },
          "GET /api/v1/models/1": json(aModel({ has_custom_cover: true, edit_version: 3 })),
        }),
      });
      await userEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
      await screen.findByRole("alert");
      expect(screen.getByRole("button", { name: "Reset to automatic" })).toBeDisabled();
      expect(writes).toBe(1);
      await userEvent.click(screen.getByRole("button", { name: "Review latest version" }));
      const apply = await screen.findByRole("button", { name: "Apply my cover to this version" });
      expect(writes).toBe(1);
      await userEvent.click(apply);
      await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
      expect(writes).toBe(2);
      expect(matches[1]).toContain("-v3");
      expect(screen.getByText("Saved automatic cover")).toBeVisible();
    },
  );

  it("prevents a reviewed write after permission is lost", async () => {
    renderApp(<Editor initial={aModel({ has_custom_cover: true })} />, {
      routes: routes({
        "DELETE /api/v1/models/1/cover": json({ detail: "edit_conflict" }, 412),
        "GET /api/v1/models/1": json(aModel({ effective_role: "view", edit_version: 2 })),
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
    await userEvent.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByRole("button", { name: "Apply my cover to this version" }),
    ).toBeDisabled();
  });

  it("keeps the draft available after a rejected image", async () => {
    renderApp(<Editor />, {
      routes: routes({
        "PUT /api/v1/models/1/cover": json({ detail: "model_cover_invalid" }, 422),
      }),
    });
    await userEvent.upload(
      screen.getByLabelText("Upload image"),
      new File(["png"], "bad.png", { type: "image/png" }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("could not be saved");
    expect(screen.getByRole("button", { name: "Upload image" })).toBeEnabled();
  });

  it("discards a pending acknowledgement after logout", async () => {
    let complete: (value: Response) => void = () => {
      throw new Error("No pending request");
    };
    const pending = new Promise<Response>((resolve) => {
      complete = resolve;
    });
    renderApp(<Editor initial={aModel({ has_custom_cover: true })} />, {
      routes: routes({
        "DELETE /api/v1/models/1/cover": () => pending,
      }),
    });
    await userEvent.click(screen.getByRole("button", { name: "Reset to automatic" }));
    act(() => clearLogin());
    await act(async () => complete(json(aModel({ edit_version: 2 }))));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByText("Saved custom cover")).toBeVisible();
  });

  it("retries unavailable source images", async () => {
    const app = renderApp(<Editor />, {
      routes: routes({
        "GET /api/v1/models/1/provenance": json({ detail: "unavailable" }, 503),
      }),
    });
    expect(await screen.findByRole("alert")).toHaveTextContent("Source images could not be loaded");
    app.route({ "GET /api/v1/models/1/provenance": json(aModelProvenance({ sources: [] })) });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No source images available.")).toBeVisible();
  });
});
