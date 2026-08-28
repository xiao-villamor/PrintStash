/*
 * The page frame: the scroll container and the canonical content width.
 *
 * The width choice is the only decision it makes, and it is a real one — `prose` is
 * the reading measure long-form views use, and a document view that silently falls
 * back to the full-bleed width becomes unreadable at desktop sizes. The scroll
 * container is the other half: it, not the body, is what scrolls, which is what
 * makes the overlay scroll lock work at all.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PageContainer } from "../page-container";

describe("PageContainer", () => {
  it("renders its children inside the scroll container", () => {
    render(<PageContainer>Body</PageContainer>);

    expect(screen.getByText("Body").parentElement).toHaveClass("overflow-y-auto");
  });

  it("uses the full page width by default", () => {
    render(<PageContainer>Body</PageContainer>);

    expect(screen.getByText("Body")).toHaveClass("max-w-screen-2xl");
  });

  it("narrows to a reading measure for prose", () => {
    render(<PageContainer width="prose">Body</PageContainer>);

    expect(screen.getByText("Body")).toHaveClass("max-w-4xl");
  });

  it("lets a caller's class override the width", () => {
    render(<PageContainer className="max-w-md">Body</PageContainer>);

    const inner = screen.getByText("Body");
    expect(inner).toHaveClass("max-w-md");
    expect(inner).not.toHaveClass("max-w-screen-2xl");
  });
});
