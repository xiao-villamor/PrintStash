/*
 * Where a model came from, rendered from data a third-party page supplied.
 *
 * Every value on this tab was scraped from somebody else's website, so it is
 * attacker-shaped by construction and the two URL rows are the security half:
 * only `http`/`https` links render as links, and a canonical source URL is
 * normalized before it becomes an anchor. A `javascript:` URL that reached the
 * DOM here is stored XSS triggered by clicking a model's source link. What each
 * URL shape is judged to be is `safeHttpUrl`'s own contract, tested next to it in
 * `source-url.test.ts`; what is asserted here is that this tab consults it before
 * rendering an anchor.
 *
 * The i18n rows are the other axis, and the rule is the same as everywhere: the
 * *interface* is translated, the captured values are not. A provider name, a tag,
 * a scraped title are the user's or the source's words — translating them
 * corrupts the record. The English origin labels are the deliberate fallback when
 * no provider is known, not a missing translation.
 *
 * Cover controls stay out of a view-only tab. Rendering them for a user without
 * write access offers an action that 403s.
 */

import "@testing-library/jest-dom/vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelProvenanceRead, ProvenanceFieldRead } from "@/types";
import { SourceTab } from "@/components/model-detail/source-tab";
import { renderApp, renderApp as render, json } from "@/test-support/render";
import { aModel } from "@/test-support/factories";
import { queryKeys } from "@/lib/query-client";
import type { SourceApi } from "@/features/library/provenance";
import { getMessageCatalog } from "@/lib/i18n";

/** Written as a code point so the fixture survives every editor and diff tool. */
const NUL = String.fromCharCode(0);

const deleteCover = vi.fn<SourceApi["deleteCover"]>();
const getModel = vi.fn<SourceApi["getModel"]>();
const getCoverContentPath = vi.fn<SourceApi["getCoverContentPath"]>(() => "/private-cover-content");
const getProvenance = vi.fn<SourceApi["getProvenance"]>();
const patchProvenance = vi.fn<SourceApi["patchProvenance"]>();
const putCover = vi.fn<SourceApi["putCover"]>();
const updateModel = vi.fn<SourceApi["updateModel"]>();
const api: SourceApi = {
  deleteCover,
  getModel,
  getCoverContentPath,
  getProvenance,
  patchProvenance,
  putCover,
  updateModel,
};

const provenance: ModelProvenanceRead = {
  edit_version: 1,
  sources: [
    {
      id: 8,
      provider: "printables",
      source_item_id: "41",
      canonical_url: "https://www.printables.com/model/41",
      source_revision: null,
      tags: ["calibration"],
      first_captured_at: "2026-08-24T00:00:00Z",
      last_checked_at: "2026-08-24T00:00:00Z",
      captures: [],
      cover: null,
      fields: [
        {
          field_name: "title",
          captured_value: "Source title",
          captured_origin: "confirmed",
          user_value: null,
          user_override_set: false,
          effective_value: "Source title",
          effective_origin: "confirmed",
          captured_at: null,
          user_updated_at: null,
        },
        {
          field_name: "description",
          captured_value: "Inferred description",
          captured_origin: "inferred",
          user_value: null,
          user_override_set: false,
          effective_value: "Inferred description",
          effective_origin: "inferred",
          captured_at: null,
          user_updated_at: null,
        },
        {
          field_name: "license_text",
          captured_value: "CC-BY",
          captured_origin: "confirmed",
          user_value: null,
          user_override_set: false,
          effective_value: "CC-BY",
          effective_origin: "confirmed",
          captured_at: null,
          user_updated_at: null,
        },
      ],
    },
  ],
};

describe("SourceTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getProvenance.mockResolvedValue(provenance);
    getModel.mockResolvedValue(aModel());
    putCover.mockResolvedValue({
      edit_version: 2,
      cover: {
        id: 9,
        provenance_source_id: 8,
        content_type: "image/webp",
        size_bytes: 10,
        updated_at: "2026-08-24T00:00:00Z",
      },
    });
  });

  it.each([
    ["a javascript: URL", "javascript:alert(1)"],
    ["a data: URL", "data:text/html,boom"],
    ["a file: URL", "file:///etc/passwd"],
    ["credentials hiding the real host", "https://user:secret@example.test/"],
    ["a control character smuggled into the path", `https://example.test/${NUL}trick`],
  ])("shows %s as text rather than as a link", async (_case, canonicalUrl) => {
    getProvenance.mockResolvedValue({
      ...provenance,
      sources: [{ ...provenance.sources[0], canonical_url: canonicalUrl }],
    });

    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    expect((await screen.findByText(canonicalUrl)).closest("a")).toBeNull();
  });

  it("normalizes a safe canonical URL into its link", async () => {
    getProvenance.mockResolvedValue({
      ...provenance,
      sources: [{ ...provenance.sources[0], canonical_url: "HTTPS://EXAMPLE.TEST/canonical" }],
    });

    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    expect(
      await screen.findByRole("link", { name: "https://example.test/canonical" }),
    ).toHaveAttribute("href", "https://example.test/canonical");
  });

  it("keeps cover controls out of a view-only Source tab", async () => {
    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    await screen.findByText("printables");
    expect(screen.queryByRole("button", { name: "Upload cover" })).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Upload cover")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Source tags")).toHaveTextContent("calibration");
  });

  it("localizes Source UI without translating provider, tag, or captured values", async () => {
    render(<SourceTab modelId={1} canEdit api={api} />, { locale: "es" });

    await screen.findByText("printables");
    expect(screen.getByRole("button", { name: "Usar título de la fuente" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Usar descripción de la fuente" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Subir portada" })).toBeInTheDocument();
    expect(screen.getByLabelText("Etiquetas de la fuente")).toHaveTextContent("calibration");
    expect(screen.getByText("Source title")).toBeInTheDocument();
    expect(screen.getAllByText("Fuente", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("Inferido", { exact: true })).toBeInTheDocument();
  });

  it("keeps English origin labels as the typed catalog fallback without a provider", async () => {
    expect(getMessageCatalog("en")["source.origin.confirmed"]).toBe("Source");
    expect(getMessageCatalog("en")["source.origin.inferred"]).toBe("Inferred");
    expect(getMessageCatalog("en")["source.origin.user"]).toBe("Edited");

    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    await screen.findByText("printables");
    expect(screen.getAllByText("Source", { exact: true }).length).toBeGreaterThan(0);
    expect(screen.getByText("Inferred", { exact: true })).toBeInTheDocument();
  });

  it("uses grouped technical rows for source identity and captured metadata", async () => {
    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    expect(await screen.findByRole("heading", { name: "Source" })).toBeInTheDocument();
    expect(screen.getByTestId("source-identity-panel")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Captured metadata" })).toBeInTheDocument();
    expect(screen.getByText("Source ID")).toBeInTheDocument();
    expect(screen.getByText("Revision")).toBeInTheDocument();
    expect(screen.getByText("Last checked")).toBeInTheDocument();
    expect(screen.getByText("Source title")).toBeInTheDocument();
    expect(screen.getByLabelText("Source tags")).toHaveTextContent("calibration");
  });

  it("prechecks a cover locally and uploads an accepted image", async () => {
    const user = userEvent.setup();
    render(<SourceTab modelId={1} canEdit api={api} />);

    const input = await screen.findByLabelText("Upload cover");
    expect(input).toHaveAttribute("accept", "image/jpeg,image/png,image/webp");
    await user.upload(input, new File(["cover"], "cover.png", { type: "image/png" }));

    await waitFor(() => {
      expect(putCover).toHaveBeenCalledWith(1, 8, expect.any(File), 1);
    });
  });

  it("uses source title and description through the Model endpoint without patching provenance", async () => {
    updateModel
      .mockResolvedValueOnce(aModel({ edit_version: 2 }))
      .mockResolvedValueOnce(aModel({ edit_version: 3 }));
    const user = userEvent.setup();
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Use source title" }));
    await user.click(screen.getByRole("button", { name: "Use source description" }));
    expect(updateModel).toHaveBeenNthCalledWith(1, 1, { name: "Source title" }, 1);
    expect(updateModel).toHaveBeenNthCalledWith(2, 1, { description: "Inferred description" }, 2);
    expect(patchProvenance).not.toHaveBeenCalled();
    expect(screen.getByText(/does not grant, interpret, or expand rights/i)).toBeInTheDocument();
  });

  /** The same provenance with the title carrying a user override. */
  function withOverriddenTitle(): ModelProvenanceRead {
    return {
      ...provenance,
      sources: provenance.sources.map((source) => ({
        ...source,
        fields: source.fields.map((field) =>
          field.field_name === "title"
            ? {
                ...field,
                effective_value: "Edited title",
                effective_origin: "user" satisfies ProvenanceFieldRead["effective_origin"],
                user_value: "Edited title",
                user_override_set: true,
              }
            : field,
        ),
      })),
    };
  }

  it("names each field's edit control after that field", async () => {
    // Every row renders one, so the visible word alone leaves a screen-reader
    // user hearing "Edit" five times over — and it is what let a Playwright test
    // drive the wrong one for months.
    getProvenance.mockResolvedValue(withOverriddenTitle());

    render(<SourceTab modelId={1} canEdit api={api} />);

    expect(await screen.findByRole("button", { name: "Edit Title" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Description" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit License" })).toBeInTheDocument();
  });

  it("shows the overridden value rather than the captured one", async () => {
    getProvenance.mockResolvedValue(withOverriddenTitle());

    render(<SourceTab modelId={1} canEdit api={api} />);

    expect(await screen.findByText("Edited title")).toBeInTheDocument();
  });

  it("saves an edited field as a user override", async () => {
    const user = userEvent.setup();
    const overridden = withOverriddenTitle();
    getProvenance.mockResolvedValue(overridden);
    patchProvenance.mockResolvedValue({ ...overridden, edit_version: 2 });
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    const input = screen.getByRole("textbox", { name: "Title override" });
    await user.clear(input);
    await user.type(input, "Edited title");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchProvenance).toHaveBeenCalledWith(
        1,
        8,
        {
          overrides: { title: "Edited title" },
          clear_overrides: [],
        },
        1,
      );
    });
  });

  it("clears the override when the captured value is restored", async () => {
    const user = userEvent.setup();
    getProvenance.mockResolvedValue(withOverriddenTitle());
    patchProvenance.mockResolvedValue({ ...provenance, edit_version: 2 });
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));

    await user.click(screen.getByRole("button", { name: "Restore captured value" }));
    await user.click(screen.getByRole("button", { name: /^Restore$/ }));

    await waitFor(() => {
      expect(patchProvenance).toHaveBeenCalledWith(
        1,
        8,
        {
          overrides: {},
          clear_overrides: ["title"],
        },
        1,
      );
    });
  });
  it("refuses a cover in a format the vault does not store", async () => {
    // The picker's `accept` is a hint the OS can be told to ignore; the check
    // has to happen here or a GIF reaches the server and 415s after the upload.
    const user = userEvent.setup({ applyAccept: false });
    render(<SourceTab modelId={1} canEdit api={api} />);
    const input = await screen.findByLabelText("Upload cover");

    await user.upload(input, new File(["x"], "cover.gif", { type: "image/gif" }));

    expect(putCover).not.toHaveBeenCalled();
  });

  it("refuses a cover larger than the limit", async () => {
    // Rejecting after the bytes have gone up wastes the upload and the wait.
    const user = userEvent.setup();
    render(<SourceTab modelId={1} canEdit api={api} />);
    const input = await screen.findByLabelText("Upload cover");
    const huge = new File([new Uint8Array(1)], "cover.png", { type: "image/png" });
    Object.defineProperty(huge, "size", { value: 16 * 1024 * 1024 });

    await user.upload(input, huge);

    expect(putCover).not.toHaveBeenCalled();
  });
});

describe("SourceTab conditional editing", () => {
  it("distinguishes a failed read from empty provenance", async () => {
    const user = userEvent.setup();
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json({ detail: "unavailable" }, 503),
      },
    });
    expect(await screen.findByRole("alert")).toBeInTheDocument();
    view.route({ "GET /api/v1/models/1/provenance": json(provenance) });
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Source title")).toBeInTheDocument();
  });

  it("reads source covers from the shared snapshot", async () => {
    const snapshot = {
      ...provenance,
      sources: [
        {
          ...provenance.sources[0],
          cover: {
            id: 9,
            provenance_source_id: 8,
            content_type: "image/webp",
            size_bytes: 10,
            updated_at: "2026-08-24T00:00:00Z",
          },
        },
      ],
    };
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: { "GET /api/v1/models/1/provenance": json(snapshot) },
    });
    expect(await screen.findByRole("button", { name: "Replace cover" })).toBeInTheDocument();
    expect(view.requests().filter((r) => r.url.endsWith("/cover"))).toHaveLength(0);
  });

  it("freezes the version when a field draft opens", async () => {
    const user = userEvent.setup();
    let base: string | null = null;
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "PATCH /api/v1/models/1/provenance/8": (_url, init) => {
          base = new Headers(init?.headers).get("If-Match");
          return json({ detail: "edit_conflict" }, 412);
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    await user.clear(screen.getByRole("textbox", { name: "Title override" }));
    await user.type(screen.getByRole("textbox", { name: "Title override" }), "My draft");
    await act(async () => {
      view.client.setQueryData([...queryKeys.model(1), "provenance"], {
        ...provenance,
        edit_version: 7,
      });
    });
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(base).toBe('"model-1-v1"'));
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue("My draft");
  });

  it.each([
    { label: "conflict", status: 412 },
    { label: "unknown outcome", status: 503 },
    { label: "lost response", status: 0 },
  ])("retries the retained draft after $label review", async ({ status }) => {
    const user = userEvent.setup();
    const writes: (string | null)[] = [];
    const latest = {
      ...provenance,
      edit_version: 7,
      sources: [
        {
          ...provenance.sources[0],
          fields: [{ ...provenance.sources[0].fields[0], effective_value: "Other editor" }],
        },
      ],
    };
    const accepted = {
      ...latest,
      edit_version: 11,
      sources: [
        {
          ...latest.sources[0],
          fields: [{ ...latest.sources[0].fields[0], effective_value: "My draft" }],
        },
      ],
    };
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "GET /api/v1/models/1": json(aModel({ id: 1, edit_version: 7, effective_role: "edit" })),
        "PATCH /api/v1/models/1/provenance/8": (_url, init) => {
          writes.push(new Headers(init?.headers).get("If-Match"));
          if (writes.length === 1 && status === 0) throw new TypeError("Lost response");
          return writes.length === 1 ? json({ detail: "edit_conflict" }, status) : json(accepted);
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    await user.clear(screen.getByRole("textbox", { name: "Title override" }));
    await user.type(screen.getByRole("textbox", { name: "Title override" }), "My draft");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Review latest version" });
    expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
    expect(writes).toHaveLength(1);
    view.route({ "GET /api/v1/models/1/provenance": json(latest) });
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("Other editor")).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue("My draft");
    await user.click(screen.getByRole("button", { name: "Save my draft against this version" }));
    expect(await screen.findByText("My draft")).toBeInTheDocument();
    expect(writes).toEqual(['"model-1-v1"', '"model-1-v7"']);
  });
});

describe("SourceTab conflict recovery", () => {
  async function openConflict(
    options: {
      status?: number;
      version?: number;
      role?: "view" | "edit";
      reviewStatus?: number;
    } = {},
  ) {
    const user = userEvent.setup();
    const latest = {
      ...provenance,
      edit_version: 7,
      sources: [
        {
          ...provenance.sources[0],
          fields: [{ ...provenance.sources[0].fields[0], effective_value: "Latest saved title" }],
        },
      ],
    };
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "GET /api/v1/models/1": options.reviewStatus
          ? json({ detail: "review_failed" }, options.reviewStatus)
          : json(
              aModel({
                id: 1,
                edit_version: options.version ?? 7,
                effective_role: options.role ?? "edit",
              }),
            ),
        "PATCH /api/v1/models/1/provenance/8":
          options.status === 0
            ? () => {
                throw new TypeError("Lost response");
              }
            : json({ detail: "edit_conflict" }, options.status ?? 412),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    await user.clear(screen.getByRole("textbox", { name: "Title override" }));
    await user.type(screen.getByRole("textbox", { name: "Title override" }), "My draft");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByRole("button", { name: "Review latest version" });
    view.route({ "GET /api/v1/models/1/provenance": json(latest) });
    return { user, view, latest };
  }

  it("adopts the reviewed Source without another write", async () => {
    const { user, view } = await openConflict();
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    await user.click(await screen.findByRole("button", { name: "Use latest version" }));
    expect(await screen.findByText("Latest saved title")).toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Title override" })).not.toBeInTheDocument();
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
  });

  it("refuses an incoherent review", async () => {
    const { user, view } = await openConflict({ version: 8 });
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    await screen.findByText("The source changed during review. Review the latest version again.");
    await waitFor(() =>
      expect(view.requests().filter((r) => r.url === "/api/v1/models/1")).toHaveLength(1),
    );
    expect(
      screen.queryByRole("button", { name: "Save my draft against this version" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue("My draft");
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
  });

  it("preserves a draft when review fails", async () => {
    const { user, view } = await openConflict({ reviewStatus: 503 });
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    await waitFor(() =>
      expect(view.requests().filter((r) => r.url === "/api/v1/models/1")).toHaveLength(1),
    );
    expect(
      screen.queryByRole("button", { name: "Save my draft against this version" }),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue("My draft");
    view.route({ "GET /api/v1/models/1": json(aModel({ edit_version: 7 })) });
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    expect(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    ).toBeEnabled();
  });

  it.each([401, 403, 404])(
    "hides private Source data after denied review %s",
    async (reviewStatus) => {
      const { user } = await openConflict({ reviewStatus });
      await user.click(screen.getByRole("button", { name: "Review latest version" }));
      await waitFor(() =>
        expect(screen.queryByRole("textbox", { name: "Title override" })).not.toBeInTheDocument(),
      );
      expect(screen.queryByText("printables")).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument();
    },
  );

  it("refuses retry after edit permission is revoked", async () => {
    const { user, view } = await openConflict({ role: "view" });
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    expect(await screen.findByText("Latest saved title")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save my draft against this version" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Use latest version" }));
    expect(screen.queryByRole("button", { name: "Edit Title" })).not.toBeInTheDocument();
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
  });

  it("reviews a repeated conflict", async () => {
    const { user, view } = await openConflict();
    await user.click(screen.getByRole("button", { name: "Review latest version" }));
    await user.click(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("button", { name: "Save my draft against this version" }),
      ).not.toBeInTheDocument(),
    );
    expect(screen.getByRole("button", { name: "Review latest version" })).toBeEnabled();
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue("My draft");
    expect(view.requestsWithMethod("PATCH")).toHaveLength(2);
  });

  it("retains the selected cover for explicit retry", async () => {
    const user = userEvent.setup();
    const uploads: { base: string | null; file: FormDataEntryValue | null }[] = [];
    const file = new File(["pixels"], "selected.png", { type: "image/png" });
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "GET /api/v1/models/1": json(aModel({ edit_version: 7 })),
        "PUT /api/v1/models/1/provenance/8/cover": (_url, init) => {
          if (!(init?.body instanceof FormData)) throw new Error("Expected multipart upload");
          uploads.push({
            base: new Headers(init.headers).get("If-Match"),
            file: init.body.get("file"),
          });
          if (uploads.length === 1) return json({ detail: "edit_conflict" }, 412);
          const response = json({
            id: 9,
            provenance_source_id: 8,
            content_type: "image/webp",
            size_bytes: 12,
            updated_at: "2026-08-24T00:00:00Z",
          });
          response.headers.set("ETag", '"model-1-v12"');
          return response;
        },
      },
    });
    await user.upload(await screen.findByLabelText("Upload cover"), file);
    view.route({ "GET /api/v1/models/1/provenance": json({ ...provenance, edit_version: 7 }) });
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    await user.click(
      await screen.findByRole("button", { name: "Save my draft against this version" }),
    );
    expect(await screen.findByRole("button", { name: "Replace cover" })).toBeInTheDocument();
    expect(uploads.map((upload) => upload.base)).toEqual(['"model-1-v1"', '"model-1-v7"']);
    expect(uploads[0].file).toBe(file);
    expect(uploads[1].file).toBe(file);
    expect(view.client.getQueryData([...queryKeys.model(1), "provenance"])).toMatchObject({
      edit_version: 12,
    });
  });

  it.each([
    { label: "title", button: "Use source title", payload: { name: "Source title" } },
    {
      label: "description",
      button: "Use source description",
      payload: { description: "Inferred description" },
    },
  ])("applies captured Model $label conditionally", async ({ button, payload }) => {
    const user = userEvent.setup();
    let base: string | null = null;
    const saved = aModel({ edit_version: 8, ...payload });
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "PATCH /api/v1/models/1": (_url, init) => {
          base = new Headers(init?.headers).get("If-Match");
          return json(saved);
        },
      },
    });
    await user.click(await screen.findByRole("button", { name: button }));
    await waitFor(() => expect(view.client.getQueryData(queryKeys.model(1))).toMatchObject(saved));
    expect(base).toBe('"model-1-v1"');
    expect(JSON.parse(view.requestsWithMethod("PATCH")[0].body)).toEqual(payload);
    expect(view.client.getQueryData([...queryKeys.model(1), "provenance"])).toMatchObject({
      edit_version: 8,
    });
  });

  it("reviews the current Model value before replacing it with captured text", async () => {
    const user = userEvent.setup();
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "GET /api/v1/models/1": json(aModel({ edit_version: 7, name: "Current model title" })),
        "PATCH /api/v1/models/1": json({ detail: "edit_conflict" }, 412),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Use source title" }));
    view.route({ "GET /api/v1/models/1/provenance": json({ ...provenance, edit_version: 7 }) });
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    expect(
      within(await screen.findByRole("region", { name: "Latest saved version" })).getByText(
        "Current model title",
      ),
    ).toBeInTheDocument();
    expect(view.requestsWithMethod("PATCH")).toHaveLength(1);
  });
});

describe("SourceTab edit boundaries", () => {
  it("preserves an editable draft after validation rejection", async () => {
    const user = userEvent.setup();
    renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "PATCH /api/v1/models/1/provenance/8": json({ detail: "invalid_override" }, 422),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    await user.type(screen.getByRole("textbox", { name: "Title override" }), " draft");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Save" })).toBeEnabled());
    expect(screen.getByRole("textbox", { name: "Title override" })).toHaveValue(
      "Source title draft",
    );
    expect(screen.queryByRole("button", { name: "Review latest version" })).not.toBeInTheDocument();
  });

  it("hides cached Source data after a denied refresh", async () => {
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: { "GET /api/v1/models/1/provenance": json(provenance) },
    });
    await screen.findByText("Source title");
    view.route({ "GET /api/v1/models/1/provenance": json({ detail: "forbidden" }, 403) });
    await act(async () => {
      await view.client.refetchQueries({ queryKey: [...queryKeys.model(1), "provenance"] });
    });
    await waitFor(() => expect(screen.queryByText("Source title")).not.toBeInTheDocument());
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("retires a draft when changing Model identity", async () => {
    const user = userEvent.setup();
    const held = Promise.withResolvers<Response>();
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(provenance),
        "PATCH /api/v1/models/1/provenance/8": () => held.promise,
        "GET /api/v1/models/2/provenance": json({ ...provenance, edit_version: 12 }),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    await user.click(screen.getByRole("button", { name: "Save" }));
    view.rerender(<SourceTab modelId={2} canEdit />);
    await screen.findByText("Source title");
    expect(screen.getByRole("button", { name: "Edit Title" })).toBeEnabled();
    await act(async () => held.resolve(json({ ...provenance, edit_version: 9 })));
    expect(screen.queryByRole("textbox", { name: "Title override" })).not.toBeInTheDocument();
    expect(view.client.getQueryData([...queryKeys.model(2), "provenance"])).toMatchObject({
      edit_version: 12,
    });
  });

  it.each([
    { label: "replace", method: "PUT" },
    { label: "delete", method: "DELETE" },
  ])("freezes the cover $label confirmation version", async ({ method }) => {
    const user = userEvent.setup();
    const snapshot = {
      ...provenance,
      sources: [
        {
          ...provenance.sources[0],
          cover: {
            id: 9,
            provenance_source_id: 8,
            content_type: "image/webp",
            size_bytes: 10,
            updated_at: "2026-08-24T00:00:00Z",
          },
        },
      ],
    };
    let base: string | null = null;
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(snapshot),
        [`${method} /api/v1/models/1/provenance/8/cover`]: (_url, init) => {
          base = new Headers(init?.headers).get("If-Match");
          return json({ detail: "edit_conflict" }, 412);
        },
      },
    });
    if (method === "PUT")
      await user.upload(
        await screen.findByLabelText("Replace cover"),
        new File(["x"], "new.png", { type: "image/png" }),
      );
    else await user.click(await screen.findByRole("button", { name: "Delete cover" }));
    const dialog = screen.getByRole("dialog");
    act(() => {
      view.client.setQueryData([...queryKeys.model(1), "provenance"], {
        ...snapshot,
        edit_version: 7,
      });
    });
    await user.click(
      within(dialog).getByRole("button", {
        name: method === "PUT" ? "Replace cover" : "Delete cover",
      }),
    );
    await screen.findByRole("button", { name: "Review latest version" });
    expect(base).toBe('"model-1-v1"');
  });
});

describe("SourceTab cover review", () => {
  it("requests the reviewed cover version", async () => {
    const user = userEvent.setup();
    const firstCover = {
      id: 9,
      provenance_source_id: 8,
      content_type: "image/webp",
      size_bytes: 10,
      updated_at: "2026-08-24T00:00:00Z",
    };
    const snapshot = { ...provenance, sources: [{ ...provenance.sources[0], cover: firstCover }] };
    const latest = {
      ...provenance,
      edit_version: 7,
      sources: [
        { ...provenance.sources[0], cover: { ...firstCover, updated_at: "2026-08-25T00:00:00Z" } },
      ],
    };
    const view = renderApp(<SourceTab modelId={1} canEdit />, {
      routes: {
        "GET /api/v1/models/1/provenance": json(snapshot),
        "GET /api/v1/models/1/provenance/8/cover/content": new Response("image", {
          headers: { "Content-Type": "image/webp" },
        }),
        "GET /api/v1/models/1": json(aModel({ edit_version: 7 })),
        "DELETE /api/v1/models/1/provenance/8/cover": json({ detail: "edit_conflict" }, 412),
      },
    });
    await user.click(await screen.findByRole("button", { name: "Delete cover" }));
    await user.click(
      within(screen.getByRole("dialog")).getByRole("button", { name: "Delete cover" }),
    );
    view.route({ "GET /api/v1/models/1/provenance": json(latest) });
    await user.click(await screen.findByRole("button", { name: "Review latest version" }));
    const imageReads = () => view.requests().filter((r) => r.url.includes("/cover/content"));
    await waitFor(() => expect(imageReads()).toHaveLength(2));
    expect(imageReads()[0].url).not.toBe(imageReads()[1].url);
    await user.click(screen.getByRole("button", { name: "Use latest version" }));
    await screen.findByRole("button", { name: "Delete cover" });
    expect(imageReads()).toHaveLength(2);
  });
});
