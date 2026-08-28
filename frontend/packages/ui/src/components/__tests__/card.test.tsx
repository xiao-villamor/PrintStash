/*
 * The card shell and its six slots, used for every model, printer, and spool tile.
 *
 * There is no logic here, which is exactly why the tests are about structure: each
 * part must render the semantic element the layout and the accessibility tree expect
 * — `CardTitle` is a heading, `CardDescription` is a paragraph — and each must
 * forward its ref, because the grid virtualiser and the drag handlers measure these
 * nodes directly. A part quietly rendering a `div` where a heading belongs removes it
 * from every screen reader's document outline without changing a pixel.
 */

import { render, screen } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../card";

describe("Card", () => {
  it("renders its slots in order", () => {
    render(
      <Card>
        <CardHeader>
          <CardTitle>Benchy</CardTitle>
          <CardDescription>A calibration boat.</CardDescription>
        </CardHeader>
        <CardContent>Two files</CardContent>
        <CardFooter>Updated today</CardFooter>
      </Card>,
    );

    expect(screen.getByRole("heading", { name: "Benchy" })).toBeInTheDocument();
    expect(screen.getByText("A calibration boat.").tagName).toBe("P");
    expect(screen.getByText("Two files")).toBeInTheDocument();
    expect(screen.getByText("Updated today")).toBeInTheDocument();
  });

  it("lets a caller's class override the shell's own", () => {
    render(<Card className="rounded-none">Body</Card>);

    const card = screen.getByText("Body");
    expect(card).toHaveClass("rounded-none");
    expect(card).not.toHaveClass("rounded-lg");
  });

  it("forwards a ref from every part", () => {
    const refs = {
      card: createRef<HTMLDivElement>(),
      header: createRef<HTMLDivElement>(),
      title: createRef<HTMLParagraphElement>(),
      description: createRef<HTMLParagraphElement>(),
      content: createRef<HTMLDivElement>(),
      footer: createRef<HTMLDivElement>(),
    };

    render(
      <Card ref={refs.card}>
        <CardHeader ref={refs.header}>
          <CardTitle ref={refs.title}>Benchy</CardTitle>
          <CardDescription ref={refs.description}>A calibration boat.</CardDescription>
        </CardHeader>
        <CardContent ref={refs.content}>Two files</CardContent>
        <CardFooter ref={refs.footer}>Updated today</CardFooter>
      </Card>,
    );

    expect(Object.values(refs).every((ref) => ref.current instanceof HTMLElement)).toBe(true);
  });
});
