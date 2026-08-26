import "@testing-library/jest-dom/vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { ModelProvenanceRead, ProvenanceFieldRead } from "@/types";
import { SourceTab, type SourceTabApi } from "@/components/model-detail/source-tab";
import { safeHttpUrl } from "@/components/model-detail/source-url";
import { I18nProvider, messageCatalogs } from "@/lib/i18n";

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

describe("SourceTab representative cover", () => {
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

  it("accepts only safe HTTP(S) provenance links", () => {
    expect(safeHttpUrl("https://example.test/creator")).toBe("https://example.test/creator");
    expect(safeHttpUrl("http://example.test/license")).toBe("http://example.test/license");
    for (const unsafe of [
      "javascript:alert(1)",
      "data:text/html,boom",
      "file:///etc/passwd",
      "https://user:secret@example.test/",
      "https://example.test/\u0000trick",
    ]) {
      expect(safeHttpUrl(unsafe)).toBeNull();
    }
  });

  it("renders only safe canonical source URLs as normalized links", async () => {
    const unsafeCanonicalUrls = [
      "javascript:alert(1)",
      "data:text/html,boom",
      "file:///etc/passwd",
      "https://user:secret@example.test/",
      "https://example.test/\u0000trick",
    ];

    for (const canonicalUrl of unsafeCanonicalUrls) {
      getProvenance.mockResolvedValue({
        sources: [{ ...provenance.sources[0], canonical_url: canonicalUrl }],
      });
      const { unmount } = render(<SourceTab modelId={1} canEdit={false} api={api} />);

      expect((await screen.findByText(canonicalUrl)).closest("a")).toBeNull();
      unmount();
    }

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

  it("displays, saves, and restores a source field override", async () => {
    const user = userEvent.setup();
    const overridden: ModelProvenanceRead = {
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
    getProvenance.mockResolvedValue(overridden);
    patchProvenance.mockResolvedValueOnce(overridden).mockResolvedValueOnce(provenance);

    render(<SourceTab modelId={1} canEdit api={api} />);

    expect(await screen.findByText("Edited title")).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    const input = screen.getByRole("textbox", { name: "Title override" });
    await user.clear(input);
    await user.type(input, "Edited title");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => {
      expect(patchProvenance).toHaveBeenNthCalledWith(1, 1, 8, {
        overrides: { title: "Edited title" },
        clear_overrides: [],
      });
    });

    await user.click(screen.getAllByRole("button", { name: "Edit" })[0]);
    await user.click(screen.getByRole("button", { name: "Restore captured value" }));
    await user.click(screen.getByRole("button", { name: /^Restore$/ }));
    await waitFor(() => {
      expect(patchProvenance).toHaveBeenNthCalledWith(2, 1, 8, {
        overrides: {},
        clear_overrides: ["title"],
      });
    });
  });
});
