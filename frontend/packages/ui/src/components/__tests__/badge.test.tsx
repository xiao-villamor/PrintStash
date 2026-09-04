/*
 * The status pill: print state, filament level, provider support tier.
 *
 * Its variants are semantic — `destructive` and `warning` are how a failed print or
 * an empty spool reads at a glance — so a variant silently falling back to the
 * default is a real regression that no layout test would notice. That, and the
 * caller's own class beating the variant's, is the whole contract.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Badge, badgeVariants } from "../badge";

describe("Badge", () => {
  it("renders its children with the default variant", () => {
    render(<Badge>Ready</Badge>);

    expect(screen.getByText("Ready")).toHaveClass("bg-primary");
  });

  it("swaps in the classes of the requested variant", () => {
    render(<Badge variant="destructive">Failed</Badge>);

    expect(screen.getByText("Failed")).toHaveClass("bg-destructive");
  });

  it("lets a caller's class override the variant's own", () => {
    render(<Badge className="rounded-none">Ready</Badge>);

    const badge = screen.getByText("Ready");
    expect(badge).toHaveClass("rounded-none");
    expect(badge).not.toHaveClass("rounded-full");
  });

  it("passes DOM attributes through", () => {
    render(<Badge title="Print state">Ready</Badge>);

    expect(screen.getByText("Ready")).toHaveAttribute("title", "Print state");
  });

  it("exposes its variants for callers that style their own element", () => {
    expect(badgeVariants({ variant: "outline" })).toContain("text-foreground");
  });
});
