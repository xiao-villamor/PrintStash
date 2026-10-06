/** Generated captions remain separate from human text and preserve edit or dismissal decisions. */
import { act, screen, waitFor, within } from "@testing-library/react";
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
  it("keeps the draft bound to its opening version after refresh", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({ detail: "caption_changed" }, 409),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Caption text" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Caption text" }), "My draft");
    app.route({
      [`GET ${url}`]: json(
        aCaption({ state: "edited", text: "Changed elsewhere", version_token: "b".repeat(32) }),
      ),
    });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["subject-caption"] });
    });
    expect(await screen.findByText("Edited")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue("My draft");
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await screen.findByRole("alert");
    expect(JSON.parse(app.requestsWithMethod("PATCH")[0].body)).toEqual({
      action: "edit",
      text: "My draft",
      version_token: "a".repeat(32),
    });
    expect(screen.getByRole("button", { name: "Save caption" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Review latest caption" })).toBeVisible();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it("keeps a failed-refresh caption draft read-only until retry", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: { [`GET ${url}`]: json(aCaption()) },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Caption text" }), " local");
    app.route({ [`GET ${url}`]: json({}, 503) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["subject-caption"] });
    });
    expect(await screen.findByText("Caption unavailable.")).toBeVisible();
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue(
      "A mounting bracket local",
    );
    expect(screen.getByRole("button", { name: "Save caption" })).toBeDisabled();
    app.route({
      [`GET ${url}`]: json(
        aCaption({ text: "Latest generated caption", version_token: "b".repeat(32) }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save caption" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue(
      "A mounting bracket local",
    );
    expect(app.requestsWithMethod("PATCH")).toHaveLength(0);
  });
  it.each([403, 404])("suppresses an inaccessible cached caption after %s", async (status) => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: { [`GET ${url}`]: json(aCaption({ text: "Private saved caption" })) },
    });
    await screen.findByText("Private saved caption");
    app.route({ [`GET ${url}`]: json({}, status) });
    await act(async () => {
      await app.client.refetchQueries({ queryKey: ["subject-caption"] });
    });
    expect(await screen.findByText("Caption unavailable.")).toBeVisible();
    expect(screen.queryByText("Private saved caption")).toBeNull();
    expect(screen.queryByRole("button", { name: "Edit caption" })).toBeNull();
  });
  it("rebases a preserved draft only after explicit latest review", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({ detail: "caption_changed" }, 409),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Caption text" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Caption text" }),
      "My retained draft",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await screen.findByRole("alert");
    app.route({
      [`GET ${url}`]: json(
        aCaption({ text: "Latest saved caption text", version_token: "b".repeat(32) }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest caption" }));
    const dialog = await screen.findByRole("dialog", { name: "Review latest caption" });
    expect(within(dialog).getByText("Latest saved caption text")).toBeVisible();
    expect(within(dialog).getByText("My retained draft")).toBeVisible();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    await userEvent.click(
      within(dialog).getByRole("button", { name: "Keep my draft against this version" }),
    );
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue("My retained draft");
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
    app.route({
      [`PATCH ${url}`]: json(
        aCaption({ state: "edited", text: "My retained draft", version_token: "c".repeat(32) }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    expect(await screen.findByText("My retained draft")).toBeVisible();
    expect(JSON.parse(app.requestsWithMethod("PATCH")[1].body)).toEqual({
      action: "edit",
      text: "My retained draft",
      version_token: "b".repeat(32),
    });
  });
  it("accepts an applied save after a lost acknowledgement without repeating it", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({}, 503),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.clear(screen.getByRole("textbox", { name: "Caption text" }));
    await userEvent.type(
      screen.getByRole("textbox", { name: "Caption text" }),
      "Already persisted draft",
    );
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: "Save caption" })).toBeDisabled();
    app.route({
      [`GET ${url}`]: json(
        aCaption({
          state: "edited",
          text: "Already persisted draft",
          version_token: "b".repeat(32),
        }),
      ),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest caption" }));
    const dialog = await screen.findByRole("dialog", { name: "Review latest caption" });
    await userEvent.click(within(dialog).getByRole("button", { name: "Use latest caption" }));
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.getByText("Already persisted draft")).toBeVisible();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it("keeps a denied editor draft read-only after rechecking access", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({}, 403),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.type(screen.getByRole("textbox", { name: "Caption text" }), " local");
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await screen.findByRole("alert");
    app.route({
      [`GET ${url}`]: json(aCaption({ can_edit: false, version_token: "b".repeat(32) })),
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest caption" }));
    const dialog = await screen.findByRole("dialog", { name: "Review latest caption" });
    expect(
      within(dialog).getByRole("button", { name: "Keep my draft against this version" }),
    ).toBeDisabled();
    await userEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveValue(
      "A mounting bracket local",
    );
    expect(screen.getByRole("textbox", { name: "Caption text" })).toHaveAttribute("readonly");
    expect(screen.getByRole("button", { name: "Save caption" })).toBeDisabled();
    expect(app.requestsWithMethod("PATCH")).toHaveLength(1);
  });
  it("discards a late latest-review result after changing subject", async () => {
    const app = renderApp(<SubjectCaption type="model" id={7} />, {
      routes: {
        [`GET ${url}`]: json(aCaption()),
        [`PATCH ${url}`]: json({}, 409),
        "GET /api/v1/subjects/model/8/caption": json(aCaption({ text: "Current subject" })),
      },
    });
    await userEvent.click(await screen.findByRole("button", { name: "Edit caption" }));
    await userEvent.click(screen.getByRole("button", { name: "Save caption" }));
    await screen.findByRole("alert");
    let finish!: (response: Response) => void;
    let signal: AbortSignal | null | undefined;
    const held = new Promise<Response>((resolve) => {
      finish = resolve;
    });
    app.route({
      [`GET ${url}`]: (_url, init) => {
        signal = init?.signal;
        return held;
      },
    });
    await userEvent.click(screen.getByRole("button", { name: "Review latest caption" }));
    await waitFor(() => expect(signal).toBeDefined());
    app.rerender(<SubjectCaption type="model" id={8} />);
    expect(await screen.findByText("Current subject")).toBeVisible();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      finish(json(aCaption({ text: "Obsolete latest text" })));
      await held;
    });
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByText("Obsolete latest text")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
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
