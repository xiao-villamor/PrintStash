/** Camera, picker and drop inputs share image validation and release replaced previews. */
import { useState } from "react";
import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { SearchImageInput } from "@/components/search-image-input";
import { renderApp } from "@/test-support/render";

function ImageControl({ enabled = true }: { enabled?: boolean }) {
  const [image, setImage] = useState<File | null>(null);
  return <SearchImageInput image={image} enabled={enabled} onChange={setImage} />;
}

describe("Search image input", () => {
  it.each([
    { label: "PNG", type: "image/png", size: 1 },
    { label: "JPEG", type: "image/jpeg", size: 1 },
    { label: "WebP", type: "image/webp", size: 1 },
    { label: "maximum size", type: "image/png", size: 8 * 1024 * 1024 },
  ])("searches with one dropped image: $label", ({ type, size }) => {
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:dropped-query");
    renderApp(<ImageControl />);
    const zone = screen.getByRole("region", { name: "Image search" });
    fireEvent.dragEnter(zone, { dataTransfer: { types: ["Files"] } });
    fireEvent.dragOver(zone, { dataTransfer: { types: ["Files"] } });
    fireEvent.drop(zone, {
      dataTransfer: {
        files: [new File([new Uint8Array(size)], "query", { type })],
        types: ["Files"],
      },
    });
    expect(screen.getByRole("img", { name: "Image used for this search" })).toHaveAttribute(
      "src",
      "blob:dropped-query",
    );
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("refuses dropped images without visual capability", () => {
    renderApp(<ImageControl enabled={false} />);
    fireEvent.drop(screen.getByRole("region", { name: "Image search" }), {
      dataTransfer: {
        files: [new File(["image"], "query.png", { type: "image/png" })],
        types: ["Files"],
      },
    });
    expect(screen.queryByRole("img")).toBeNull();
    expect(screen.getByRole("button", { name: "Take photo" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Choose image" })).toBeDisabled();
  });

  it.each([
    { label: "empty", type: "image/png", size: 0 },
    { label: "oversized", type: "image/png", size: 8 * 1024 * 1024 + 1 },
    { label: "unsupported", type: "image/gif", size: 1 },
  ])("rejects invalid dropped images: $label", ({ type, size }) => {
    renderApp(<ImageControl />);
    fireEvent.drop(screen.getByRole("region", { name: "Image search" }), {
      dataTransfer: {
        files: [new File([new Uint8Array(size)], "query", { type })],
        types: ["Files"],
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Choose a PNG, JPEG or WebP image no larger than 8 MB.",
    );
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("rejects multiple dropped images", () => {
    renderApp(<ImageControl />);
    fireEvent.drop(screen.getByRole("region", { name: "Image search" }), {
      dataTransfer: {
        files: [
          new File(["one"], "one.png", { type: "image/png" }),
          new File(["two"], "two.png", { type: "image/png" }),
        ],
        types: ["Files"],
      },
    });
    expect(screen.getByRole("alert")).toHaveTextContent("Choose one image at a time.");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("accepts a camera image through the same private input", async () => {
    const user = userEvent.setup();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:camera-query");
    renderApp(<ImageControl />);
    const camera = screen.getByLabelText("Take photo", { selector: "input" });
    expect(camera).toHaveAttribute("capture", "environment");
    await user.upload(camera, new File(["photo"], "photo.jpg", { type: "image/jpeg" }));
    expect(screen.getByRole("img", { name: "Image used for this search" })).toHaveAttribute(
      "src",
      "blob:camera-query",
    );
  });

  it("preserves a selection when the picker is cancelled", async () => {
    const user = userEvent.setup();
    vi.spyOn(URL, "createObjectURL").mockReturnValue("blob:chosen-query");
    renderApp(<ImageControl />);
    const input = screen.getByLabelText("Choose image", { selector: "input" });
    await user.upload(input, new File(["photo"], "photo.png", { type: "image/png" }));
    fireEvent.change(input, { target: { files: [] } });
    expect(screen.getByRole("img", { name: "Image used for this search" })).toHaveAttribute(
      "src",
      "blob:chosen-query",
    );
  });

  it("exposes image controls in Spanish", () => {
    renderApp(<ImageControl />, { locale: "es" });
    expect(screen.getByRole("button", { name: "Tomar foto" })).toBeVisible();
    expect(
      screen.getByText("Arrastra una imagen aquí, elige un archivo o toma una foto."),
    ).toBeVisible();
  });
});
