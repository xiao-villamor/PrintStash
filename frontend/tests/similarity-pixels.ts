import type { Page } from "@playwright/test";

/** Compare the rendered mesh silhouette, allowing subpixel canvas positioning. */
export async function silhouetteOverlap(page: Page, first: Buffer, second: Buffer) {
  return page.evaluate(
    async ([a, b]) => {
      const masks = await Promise.all(
        [a, b].map(async (bytes) => {
          const bitmap = await createImageBitmap(
            new Blob([new Uint8Array(bytes)], { type: "image/png" }),
          );
          const canvas = document.createElement("canvas");
          canvas.width = canvas.height = 256;
          const context = canvas.getContext("2d")!;
          context.drawImage(bitmap, 0, 0, 256, 256);
          bitmap.close();
          const pixels = context.getImageData(0, 0, 256, 256).data;
          return Array.from(
            { length: 256 * 256 },
            (_, index) =>
              Math.max(
                ...[0, 1, 2].map((channel) =>
                  Math.abs(pixels[index * 4 + channel] - pixels[channel]),
                ),
              ) > 30,
          );
        }),
      );
      let intersection = 0,
        union = 0;
      for (let i = 0; i < masks[0].length; i++) {
        if (masks[0][i] && masks[1][i]) intersection++;
        if (masks[0][i] || masks[1][i]) union++;
      }
      // Empty/error canvases must not count as matching geometry.
      return union > 100 ? intersection / union : 0;
    },
    [[...first], [...second]],
  );
}
