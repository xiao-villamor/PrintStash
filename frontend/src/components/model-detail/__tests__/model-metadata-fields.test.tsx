/* The detail edit form retains tag keyboard behavior and writable model fields. */
import "@testing-library/jest-dom/vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ModelMetadataFields, type ModelMetaEditor } from "../model-metadata-fields";

const editor: ModelMetaEditor = {
  collection: "",
  collectionLabel: null,
  setCollection: () => {},
  catOpen: false,
  setCatOpen: () => {},
  allowRoot: true,
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

describe("ModelMetadataFields", () => {
  describe("choosing tags while editing", () => {
    /** The overview in edit mode, with a tag vocabulary to pick from. */
    function renderEditing(over: Partial<typeof editor> = {}) {
      const setTagInput = vi.fn<(value: string) => void>();
      const toggleTag = vi.fn<(name: string) => void>();
      const createTag = vi.fn<(name: string) => void>();
      const setTags = vi.fn<React.Dispatch<React.SetStateAction<string[]>>>();
      const result = render(
        <ModelMetadataFields
          editor={{
            ...editor,
            setTagInput,
            toggleTag,
            createTag,
            setTags,
            ...over,
          }}
        />,
      );
      return { ...result, setTagInput, toggleTag, createTag, setTags };
    }

    it("offers a description field", () => {
      renderEditing();

      expect(screen.getByPlaceholderText("Optional description")).toBeInTheDocument();
    });

    it("previews the description draft", () => {
      renderEditing({ description: "## Assembly\n\n- Fit **both** clips." });
      expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(
        "## Assembly\n\n- Fit **both** clips.",
      );
      fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
      expect(screen.getByRole("heading", { name: "Assembly" })).toBeVisible();
      expect(screen.getByText("both").tagName).toBe("STRONG");
    });

    it("keeps the draft when returning to Write", () => {
      const description = "## Assembly";
      renderEditing({ description });
      fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
      expect(screen.queryByRole("textbox", { name: "Description" })).toBeNull();
      fireEvent.click(screen.getByRole("tab", { name: "Write" }));
      expect(screen.getByRole("textbox", { name: "Description" })).toHaveValue(description);
    });

    it("explains an empty preview", () => {
      renderEditing();
      fireEvent.click(screen.getByRole("tab", { name: "Preview" }));
      expect(screen.getByRole("tabpanel", { name: "Preview" })).toHaveTextContent(
        "No description yet",
      );
    });

    it("offers a source URL field", () => {
      renderEditing();

      expect(screen.getByRole("textbox", { name: "Source URL" })).toBeInTheDocument();
    });

    it("passes what the user types to the tag search", () => {
      const { setTagInput } = renderEditing();

      fireEvent.change(screen.getByRole("combobox", { name: "Model tags" }), {
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
