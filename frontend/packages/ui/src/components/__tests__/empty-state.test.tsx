/*
 * What a user sees when a list has nothing in it — an empty library, a printer with
 * no jobs, a search that matched nothing.
 *
 * Everything but the title is optional, and each optional slot is a branch: the
 * component must not render an empty paragraph or an empty action row when the caller
 * omits one, because a stray empty block in a centred column is visible as a gap. The
 * icon is decorative and must stay out of the accessibility tree, or a screen reader
 * announces an image where the user needs the message.
 */

import { render, screen } from "@testing-library/react";
import { Inbox } from "lucide-react";
import { describe, expect, it } from "vitest";

import { EmptyState } from "../empty-state";

describe("EmptyState", () => {
  it("renders the title on its own", () => {
    render(<EmptyState title="No models yet" />);

    expect(screen.getByText("No models yet")).toBeInTheDocument();
  });

  it("renders the description when there is one", () => {
    render(<EmptyState title="No models yet" description="Import a file to get started." />);

    expect(screen.getByText("Import a file to get started.")).toBeInTheDocument();
  });

  it("renders the action when there is one", () => {
    render(<EmptyState title="No models yet" action={<button type="button">Import</button>} />);

    expect(screen.getByRole("button", { name: "Import" })).toBeInTheDocument();
  });

  it("keeps the icon out of the accessibility tree", () => {
    const { container } = render(<EmptyState title="No models yet" icon={Inbox} />);

    expect(container.querySelector("svg")).toHaveAttribute("aria-hidden", "true");
  });

  it("renders nothing for the slots the caller omitted", () => {
    const { container } = render(<EmptyState title="No models yet" />);

    expect(container.querySelectorAll("p")).toHaveLength(1);
    expect(container.querySelector("svg")).toBeNull();
  });

  it("lets a caller add layout classes", () => {
    const { container } = render(<EmptyState title="No models yet" className="py-4" />);

    expect(container.firstElementChild).toHaveClass("py-4");
  });
});
