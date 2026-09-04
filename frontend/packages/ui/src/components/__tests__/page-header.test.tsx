/*
 * The heading row of every document page.
 *
 * The title is the page's only `h1`, which is what a screen reader jumps to and what
 * the document outline is built from — so it has to render as a heading, not as
 * styled text. The description and the action row are optional, and an omitted slot
 * must render nothing rather than an empty flex row, which would otherwise show up as
 * an unexplained gap under the title.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageHeader } from "../page-header";

describe("PageHeader", () => {
  it("renders the title as the page heading", () => {
    render(<PageHeader title="Library" />);

    expect(screen.getByRole("heading", { level: 1, name: "Library" })).toBeInTheDocument();
  });

  it("renders the description when there is one", () => {
    render(<PageHeader title="Library" description="Everything you have imported." />);

    expect(screen.getByText("Everything you have imported.")).toBeInTheDocument();
  });

  it("renders the actions when there are some", () => {
    render(<PageHeader title="Library" actions={<button type="button">Import</button>} />);

    expect(screen.getByRole("button", { name: "Import" })).toBeInTheDocument();
  });

  it("renders nothing for the slots the caller omitted", () => {
    const { container } = render(<PageHeader title="Library" />);

    expect(container.querySelector("p")).toBeNull();
    expect(container.querySelector("h1")?.parentElement?.nextElementSibling).toBeNull();
  });
});
