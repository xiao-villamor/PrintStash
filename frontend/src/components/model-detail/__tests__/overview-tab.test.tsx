/*
 * The legacy source link on the overview tab, and the scheme check on it.
 *
 * `source_url` on a Model is the pre-provenance field: a URL a user pasted or an
 * older release scraped, stored with no validation at all. Rendering it as an
 * anchor without checking the scheme is stored XSS — a `javascript:` URL that
 * fires when somebody clicks through to where their model came from.
 *
 * Safe URLs are normalized rather than passed through, so the two cases here are
 * the whole contract: `http`/`https` become links, everything else becomes text.
 */

import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { OverviewTab, type ModelMetaEditor } from "@/components/model-detail/overview-tab";
import type { ModelRead } from "@/types";

const editor: ModelMetaEditor = {
  collection: "",
  setCollection: () => {},
  catOpen: false,
  setCatOpen: () => {},
  collections: [],
  description: "",
  setDescription: () => {},
  sourceUrl: "",
  setSourceUrl: () => {},
  tagInput: "",
  setTagInput: () => {},
  tags: [],
  setTags: () => {},
  toggleTag: () => {},
  createTag: () => {},
  deleteTag: () => {},
  filteredTags: [],
  canCreate: false,
};

const model: ModelRead = {
  id: 1,
  name: "Calibration cube",
  slug: "calibration-cube",
  hash: "hash",
  collection: null,
  collection_id: null,
  description: null,
  source_url: null,
  effective_role: "admin",
  tags: [],
  thumbnail_url: null,
  created_at: "2026-08-24T00:00:00Z",
  updated_at: "2026-08-24T00:00:00Z",
  files: [],
  starred: false,
};

function renderOverview(sourceUrl: string) {
  return render(
    <OverviewTab
      model={{ ...model, source_url: sourceUrl }}
      editing={false}
      editor={editor}
      recommendedFile={null}
      hasGcode={false}
      revisionSaving={null}
      onSend={() => {}}
      canSend={false}
      onCompare={() => {}}
      onMark={() => {}}
      onAddRevision={() => {}}
    />,
  );
}

describe("OverviewTab", () => {
  it("renders a normalized safe HTTP(S) source URL as a link", () => {
    renderOverview("HTTPS://EXAMPLE.TEST/cube");

    expect(screen.getByRole("link", { name: "Source model" })).toHaveAttribute(
      "href",
      "https://example.test/cube",
    );
  });

  it("does not render unsafe source URLs as links", () => {
    renderOverview("https://user:secret@example.test/cube");

    expect(screen.queryByRole("link", { name: "Source model" })).not.toBeInTheDocument();
  });
  describe("choosing tags while editing", () => {
    /** The overview in edit mode, with a tag vocabulary to pick from. */
    function renderEditing(over: Partial<typeof editor> = {}) {
      const setTagInput = vi.fn<(value: string) => void>();
      const toggleTag = vi.fn<(name: string) => void>();
      const createTag = vi.fn<(name: string) => void>();
      const setTags = vi.fn<React.Dispatch<React.SetStateAction<string[]>>>();
      const result = render(
        <OverviewTab
          model={model}
          editing
          editor={{
            ...editor,
            setTagInput,
            toggleTag,
            createTag,
            setTags,
            ...over,
          }}
          recommendedFile={null}
          hasGcode={false}
          revisionSaving={null}
          onSend={() => {}}
          canSend={false}
          onCompare={() => {}}
          onMark={() => {}}
          onAddRevision={() => {}}
        />,
      );
      return { ...result, setTagInput, toggleTag, createTag, setTags };
    }

    it("offers a description field", () => {
      renderEditing();

      expect(screen.getByPlaceholderText("Optional description")).toBeInTheDocument();
    });

    it("offers a source URL field", () => {
      renderEditing();

      expect(
        screen.getByPlaceholderText("https://www.printables.com/model/..."),
      ).toBeInTheDocument();
    });

    it("passes what the user types to the tag search", () => {
      const { setTagInput } = renderEditing();

      fireEvent.change(screen.getByPlaceholderText(/Search or create/), {
        target: { value: "func" },
      });

      expect(setTagInput).toHaveBeenCalledWith("func");
    });

    it("suggests a matching tag", () => {
      renderEditing({
        tagInput: "func",
        filteredTags: [{ id: 1, name: "functional", slug: "functional", model_count: 3 }],
      });

      expect(screen.getByText("functional")).toBeInTheDocument();
    });

    it("offers to create a tag that does not exist yet", () => {
      // Tagging at edit time is the moment the user remembers what the model
      // was for; sending them elsewhere to define the tag first loses that.
      renderEditing({ tagInput: "spares", canCreate: true });

      expect(screen.getByText(/spares/)).toBeInTheDocument();
    });

    it("removes the last tag on backspace in an empty field", () => {
      // It is the standard gesture for chip inputs, and the only one that does
      // not require aiming at a small × on every chip.
      const { setTags } = renderEditing({ tags: ["functional"], tagInput: "" });

      fireEvent.keyDown(screen.getByPlaceholderText(/Search or create/), { key: "Backspace" });

      expect(setTags).toHaveBeenCalled();
    });

    it("leaves the tags alone when backspace has text to delete", () => {
      const { setTags } = renderEditing({ tags: ["functional"], tagInput: "fu" });

      fireEvent.keyDown(screen.getByPlaceholderText(/Search or create/), { key: "Backspace" });

      expect(setTags).not.toHaveBeenCalled();
    });
  });
});
