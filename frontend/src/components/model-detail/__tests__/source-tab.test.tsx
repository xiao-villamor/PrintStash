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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelProvenanceRead, ProvenanceFieldRead } from "@/types";
import { SourceTab, type SourceTabApi } from "@/components/model-detail/source-tab";
import { I18nProvider, messageCatalogs } from "@/lib/i18n";

/** Written as a code point so the fixture survives every editor and diff tool. */
const NUL = String.fromCharCode(0);

const deleteCover = vi.fn<SourceTabApi["deleteCover"]>();
const getCover = vi.fn<SourceTabApi["getCover"]>();
const getCoverContentPath = vi.fn<SourceTabApi["getCoverContentPath"]>(
  () => "/private-cover-content",
);
const getProvenance = vi.fn<SourceTabApi["getProvenance"]>();
const patchProvenance = vi.fn<SourceTabApi["patchProvenance"]>();
const putCover = vi.fn<SourceTabApi["putCover"]>();
const updateModel = vi.fn<SourceTabApi["updateModel"]>();
const api: SourceTabApi = {
  deleteCover,
  getCover,
  getCoverContentPath,
  getProvenance,
  patchProvenance,
  putCover,
  updateModel,
};

const provenance: ModelProvenanceRead = {
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
    getCover.mockRejectedValue(new Error("source_cover_not_found"));
    putCover.mockResolvedValue({
      id: 9,
      provenance_source_id: 8,
      content_type: "image/webp",
      size_bytes: 10,
      updated_at: "2026-08-24T00:00:00Z",
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
      sources: [{ ...provenance.sources[0], canonical_url: canonicalUrl }],
    });

    render(<SourceTab modelId={1} canEdit={false} api={api} />);

    expect((await screen.findByText(canonicalUrl)).closest("a")).toBeNull();
  });

  it("normalizes a safe canonical URL into its link", async () => {
    getProvenance.mockResolvedValue({
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
    localStorage.setItem("printstash.locale", "es");
    render(
      <I18nProvider>
        <SourceTab modelId={1} canEdit api={api} />
      </I18nProvider>,
    );

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
    expect(messageCatalogs.en["source.origin.confirmed"]).toBe("Source");
    expect(messageCatalogs.en["source.origin.inferred"]).toBe("Inferred");
    expect(messageCatalogs.en["source.origin.user"]).toBe("Edited");

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
      expect(putCover).toHaveBeenCalledWith(1, 8, expect.any(File));
    });
  });

  it("uses source title and description through the Model endpoint without patching provenance", async () => {
    // SAFETY: these actions ignore the returned Model; the API call shape is the behavior under test.
    updateModel.mockResolvedValue({} as never);
    const user = userEvent.setup();
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Use source title" }));
    await user.click(screen.getByRole("button", { name: "Use source description" }));
    expect(updateModel).toHaveBeenNthCalledWith(1, 1, { name: "Source title" });
    expect(updateModel).toHaveBeenNthCalledWith(2, 1, { description: "Inferred description" });
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
    patchProvenance.mockResolvedValue(overridden);
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));
    const input = screen.getByRole("textbox", { name: "Title override" });
    await user.clear(input);
    await user.type(input, "Edited title");

    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => {
      expect(patchProvenance).toHaveBeenCalledWith(1, 8, {
        overrides: { title: "Edited title" },
        clear_overrides: [],
      });
    });
  });

  it("clears the override when the captured value is restored", async () => {
    const user = userEvent.setup();
    getProvenance.mockResolvedValue(withOverriddenTitle());
    patchProvenance.mockResolvedValue(provenance);
    render(<SourceTab modelId={1} canEdit api={api} />);
    await user.click(await screen.findByRole("button", { name: "Edit Title" }));

    await user.click(screen.getByRole("button", { name: "Restore captured value" }));
    await user.click(screen.getByRole("button", { name: /^Restore$/ }));

    await waitFor(() => {
      expect(patchProvenance).toHaveBeenCalledWith(1, 8, {
        overrides: {},
        clear_overrides: ["title"],
      });
    });
  });
  it("refuses a cover in a format the vault does not store", async () => {
    // The picker's `accept` is a hint the OS can be told to ignore; the check
    // has to happen here or a GIF reaches the server and 415s after the upload.
    const user = userEvent.setup();
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
