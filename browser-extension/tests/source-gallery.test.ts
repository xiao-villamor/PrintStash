/** Publication images stay scoped, bounded and anonymous in extension transport. */
import { describe, expect, it } from "vitest";
import {
  sourceGallery,
  readSourceGalleryInMainWorld,
  downloadSourceImage,
} from "../source-gallery.ts";
const page = "https://www.thingiverse.com/thing:1234";
describe("source gallery", () => {
  it("deduplicates a publication image without persisting signed URL material", async () => {
    const images = await sourceGallery(page, [
      "https://cdn.thingiverse.com/a.jpg?token=temporary",
      "https://resize.thingiverse.com/?src=https%3A%2F%2Fcdn.thingiverse.com%2Fa.jpg",
    ]);
    expect(images).toHaveLength(1);
    expect(images[0].id).toMatch(/^[a-f0-9]{64}$/);
    expect(images[0].id).toBe(
      (await sourceGallery(page, ["https://cdn.thingiverse.com/a.jpg?token=rotated"]))[0].id,
    );
  });
  it("rejects arbitrary hosts and unsafe image URLs", async () => {
    expect(
      await sourceGallery(page, [
        "http://cdn.thingiverse.com/a.png",
        "https://cdn.thingiverse.com:444/a.png",
        "https://user@cdn.thingiverse.com/a.png",
        "https://cdn.thingiverse.com.evil.test/a.png",
        "http://127.0.0.1/private",
        "https://cdn.thingiverse.com/a.png#secret",
        "javascript:alert(1)",
      ]),
    ).toEqual([]);
  });
  it("does not silently truncate an oversized gallery", async () => {
    await expect(
      sourceGallery(
        page,
        Array.from({ length: 65 }, (_, i) => `https://cdn.thingiverse.com/${i}.png`),
      ),
    ).rejects.toThrow("64");
  });
  it("extracts only publication thumbnails without recommendations or avatars", () => {
    const previous = document.body.innerHTML;
    document.body.innerHTML =
      '<div class="CarouselThumbnails__carouselThumb"><img src="https://cdn.thingiverse.com/part.jpg"></div><img src="https://cdn.thingiverse.com/avatar.jpg"><div class="recommendation"><img src="https://cdn.thingiverse.com/other.jpg"></div>';
    try {
      expect(readSourceGalleryInMainWorld("thingiverse")).toEqual([
        "https://cdn.thingiverse.com/part.jpg",
      ]);
    } finally {
      document.body.innerHTML = previous;
    }
  });
  it("downloads an image without cookies or redirects", async () => {
    const [image] = await sourceGallery(page, ["https://cdn.thingiverse.com/a.png"]);
    let request: RequestInit | undefined;
    const actual = await downloadSourceImage(image, undefined, async (_url, init) => {
      request = init;
      return new Response(new Uint8Array([1, 2, 3]), {
        headers: { "Content-Type": "image/png", "Content-Length": "3" },
      });
    });
    expect(request).toMatchObject({ credentials: "omit", redirect: "error" });
    expect(actual.mediaType).toBe("image/png");
    expect([...new Uint8Array(await actual.file.arrayBuffer())]).toEqual([1, 2, 3]);
  });
  it("rejects a challenge page instead of storing it as an image", async () => {
    const [image] = await sourceGallery(page, ["https://cdn.thingiverse.com/a.png"]);
    await expect(
      downloadSourceImage(
        image,
        undefined,
        async () => new Response("challenge", { headers: { "Content-Type": "text/html" } }),
      ),
    ).rejects.toThrow("supported gallery image");
  });
  it("enforces the image limit before reading an oversized response", async () => {
    const [image] = await sourceGallery(page, ["https://cdn.thingiverse.com/a.png"]);
    await expect(
      downloadSourceImage(
        image,
        undefined,
        async () =>
          new Response("image", {
            headers: {
              "Content-Type": "image/png",
              "Content-Length": String(15 * 1024 * 1024 + 1),
            },
          }),
      ),
    ).rejects.toThrow("transfer limit");
  });
});

it("reads the complete Printables image list without provider cookies", async () => {
  const { readPrintablesGallery } = await import("../source-gallery.ts");
  let sent: RequestInit | undefined;
  const images = await readPrintablesGallery(
    "https://www.printables.com/model/949852-part/files",
    undefined,
    async (_input, init) => {
      sent = init;
      return Response.json({
        data: {
          print: {
            id: "949852",
            images: Array.from({ length: 10 }, (_, i) => ({
              filePath: `media/prints/949852/images/${i}.jpg`,
            })),
          },
        },
      });
    },
  );
  expect(images).toHaveLength(10);
  expect(images[9].url).toBe("https://media.printables.com/media/prints/949852/images/9.jpg");
  expect(sent).toMatchObject({ credentials: "omit", redirect: "error" });
  expect(JSON.parse(String(sent?.body)).variables).toEqual({ id: "949852" });
});

it("rejects a Printables image outside the selected publication", async () => {
  const { readPrintablesGallery } = await import("../source-gallery.ts");
  await expect(
    readPrintablesGallery(
      "https://www.printables.com/model/949852-part/files",
      undefined,
      async () =>
        Response.json({
          data: {
            print: { id: "949852", images: [{ filePath: "media/prints/10/images/other.jpg" }] },
          },
        }),
    ),
  ).rejects.toThrow("invalid image path");
});
