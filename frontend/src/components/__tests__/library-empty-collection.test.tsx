/*
 * An empty collection offers exactly what lands a Model in it: the upload
 * button and dropping files. It must not offer routes that add to the library
 * at large.
 */

import "@testing-library/jest-dom/vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LibraryEmptyCollection } from "@/components/library-empty-collection";

describe("LibraryEmptyCollection", () => {
  it("offers one upload action in an empty collection", async () => {
    const onUpload = vi.fn<() => void>();
    render(<LibraryEmptyCollection disabled={false} onUpload={onUpload} />);

    expect(screen.getByRole("heading", { name: "This collection is empty" })).toBeVisible();
    expect(screen.getByText("Or drop files anywhere on this page.")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "Upload files" }));

    expect(onUpload).toHaveBeenCalledOnce();
    expect(screen.getAllByRole("button")).toHaveLength(1);
    expect(screen.queryByRole("link")).not.toBeInTheDocument();
  });

  it("leaves the upload button disabled when uploading is not allowed", () => {
    render(<LibraryEmptyCollection disabled onUpload={() => {}} />);

    expect(screen.getByRole("button", { name: "Upload files" })).toBeDisabled();
  });
});
