/** Generated captions remain separate from human text and preserve edit or dismissal decisions. */
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SubjectCaption } from "@/components/subject-caption";
import { AuthContext } from "@/lib/auth-context";
import { aCaption } from "@/test-support/captions";
import { adminSession, json, memberSession, renderApp } from "@/test-support/render";

const url = "/api/v1/subjects/model/7/caption";
afterEach(() => vi.unstubAllGlobals());

describe("Subject caption", () => {
  it("hides cached captions after an identity change", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: { [`GET ${url}`]: json(aCaption({ text: "Private project caption" })) },
    });
    expect(await screen.findByText("Private project caption")).toBeVisible();
    app.route({ [`GET ${url}`]: json({ detail: "not_found" }, 404) });
    // AuthProvider observes cross-tab storage changes without a same-tab
    // auth-changed event, so its new context must fence existing query data.
    app.rerender(
      <AuthContext.Provider value={memberSession()}>
        <SubjectCaption type="model" id={7} />
      </AuthContext.Provider>,
    );
    expect(screen.queryByText("Private project caption")).toBeNull();
    expect(await screen.findByText("Caption unavailable.")).toBeVisible();
  });
  it("discards a caption draft after an identity change", async () => {
    const user = userEvent.setup();
    const app = renderApp(
      <AuthContext.Provider value={adminSession()}>
        <SubjectCaption type="model" id={7} />
      </AuthContext.Provider>,
      { routes: { [`GET ${url}`]: json(aCaption()) } },
    );
    await user.click(await screen.findByRole("button", { name: "Edit caption" }));
    await user.type(screen.getByRole("textbox"), " confidential draft");
    app.rerender(
      <AuthContext.Provider value={memberSession()}>
        <SubjectCaption type="model" id={7} />
      </AuthContext.Provider>,
    );
    expect(screen.queryByRole("textbox")).toBeNull();
  });
  it("avoids caption reads without an authenticated user", () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      auth: adminSession({ user: null }),
      routes: { [`GET ${url}`]: json(aCaption()) },
    });
    expect(app.requests()).toEqual([]);
    expect(screen.queryByRole("region", { name: "AI caption" })).toBeNull();
  });
  it("saves an edit with the displayed version", async () => {
    const user = userEvent.setup();
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json(aCaption({ state: "edited", text: "Human correction" })),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit caption" }));
    await user.clear(screen.getByRole("textbox", { name: "Caption text" }));
    await user.type(screen.getByRole("textbox", { name: "Caption text" }), "Human correction");
    await user.click(screen.getByRole("button", { name: "Save caption" }));
    expect(await screen.findByText("Edited")).toBeVisible();
    expect(screen.getByText("Human correction")).toBeVisible();
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({
      action: "edit",
      text: "Human correction",
      version_token: "a".repeat(32),
    });
  });
  it("dismisses a running caption", async () => {
    const user = userEvent.setup();
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption({ phase: "running", text: "" })),
        [`PATCH ${url}`]: json(aCaption({ state: "dismissed", text: "" })),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Dismiss caption" }));
    expect(await screen.findByText("Dismissed")).toBeVisible();
    expect(screen.queryByText(/Generating a caption/)).toBeNull();
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body).action).toBe("dismiss");
    expect(screen.getByRole("button", { name: "Generate a new caption" })).toBeVisible();
  });
  it("shows text safely to a viewer without edit controls", async () => {
    renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption({ can_edit: false, text: "<img src=x onerror=alert(1)>" })),
      },
    });
    expect(await screen.findByText("<img src=x onerror=alert(1)>")).toBeVisible();
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.queryByRole("button")).toBeNull();
  });
  it("explains unavailable generation in Spanish", async () => {
    renderApp(<SubjectCaption type="model" id={7} />, {
      locale: "es",
      routes: {
        [`GET ${url}`]: json(
          aCaption({
            state: null,
            text: "",
            can_generate: false,
            unavailable_reason: "caption_disabled",
          }),
        ),
      },
    });
    expect(
      await screen.findByText("Las descripciones automáticas están desactivadas."),
    ).toBeVisible();
    expect(screen.getByRole("region", { name: "Descripción de IA" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Generar descripción" })).toBeNull();
  });
  it("keeps the draft when a save fails", async () => {
    const user = userEvent.setup();
    renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({ detail: "caption_changed" }, 409),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit caption" }));
    await user.click(screen.getByRole("button", { name: "Save caption" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Could not save"));
    expect(screen.getByRole("textbox")).toHaveValue("A mounting bracket");
  });
});
