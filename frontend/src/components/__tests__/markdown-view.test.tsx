/*
 * The one renderer behind every piece of user-written prose in the vault.
 *
 * Document previews and collection READMEs both go through it, so a change here
 * is a change to both — and the two features are the only place a user's own
 * writing is displayed rather than their data.
 *
 * Tables are the reason it is not plain CommonMark. A parts list is the most
 * common thing anybody writes in a build guide, and pipe tables are a GFM
 * extension: without it the pipes render as literal text and the guide becomes
 * unreadable. They also have to come out as a real `<table>` with header cells,
 * not a styled grid of divs, or a screen reader cannot navigate the one piece of
 * structured content on the page.
 *
 * Links leave the app, so they carry `noopener noreferrer nofollow` and open in a
 * new tab: the source is a third-party page the user pasted, and `window.opener`
 * is a handle on the vault.
 *
 * And the input is untrusted. A README is written by anyone with edit access on
 * a collection, so raw HTML is stripped rather than rendered — a `<script>` here
 * would run with the reader's session.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { MarkdownView } from "@/components/markdown-view";

const PARTS_TABLE = `| Part | Material |
| --- | --- |
| A | PLA |
| B | PETG |`;

describe("MarkdownView", () => {
  describe("a GFM pipe table", () => {
    it("renders as a real table", () => {
      // Not a grid of divs: this is the only structured content on the page, and
      // a screen reader has no way through it otherwise.
      render(<MarkdownView source={PARTS_TABLE} />);

      expect(screen.getByRole("table")).toBeInTheDocument();
    });

    it("makes the first row into header cells", () => {
      render(<MarkdownView source={PARTS_TABLE} />);

      expect(screen.getAllByRole("columnheader").map((cell) => cell.textContent)).toEqual([
        "Part",
        "Material",
      ]);
    });

    it("keeps every body cell", () => {
      render(<MarkdownView source={PARTS_TABLE} />);

      expect(screen.getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
        "A",
        "PLA",
        "B",
        "PETG",
      ]);
    });

    it("does not leave the pipes as text", () => {
      // Which is exactly what a renderer without the GFM extension produces.
      render(<MarkdownView source={PARTS_TABLE} />);

      expect(screen.queryByText(/\| Part \| Material \|/)).toBeNull();
    });
  });

  describe("a link the user pasted", () => {
    it("keeps the href it was given", () => {
      render(<MarkdownView source="Read the [assembly guide](https://example.test/guide)." />);

      expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
        "href",
        "https://example.test/guide",
      );
    });

    it("opens it away from the vault", () => {
      render(<MarkdownView source="Read the [assembly guide](https://example.test/guide)." />);

      expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
        "target",
        "_blank",
      );
    });

    it("hands the third-party page no handle on the opener", () => {
      // `window.opener` from a page somebody else wrote is a handle on the vault.
      render(<MarkdownView source="Read the [assembly guide](https://example.test/guide)." />);

      expect(screen.getByRole("link", { name: "assembly guide" })).toHaveAttribute(
        "rel",
        "noopener noreferrer nofollow",
      );
    });
  });

  describe("raw HTML in the source", () => {
    it("renders no script the author embedded", () => {
      // A README is writable by anyone with edit access on the collection, and a
      // script here would run with the reader's session.
      render(<MarkdownView source={'<script>alert("unsafe")</script>\n\nSafe text'} />);

      expect(document.querySelector("script")).not.toBeInTheDocument();
    });

    it("still renders the prose around it", () => {
      // Stripping the tag must not take the paragraph with it, or a stray `<`
      // silently deletes somebody's notes.
      render(<MarkdownView source={'<script>alert("unsafe")</script>\n\nSafe text'} />);

      expect(screen.getByText("Safe text")).toBeInTheDocument();
    });
  });
});
