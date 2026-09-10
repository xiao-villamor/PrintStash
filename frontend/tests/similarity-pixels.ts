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
      // Empty/error canvases must not count as matching geometry.
      const foreground = masks.map((mask) => mask.filter(Boolean).length);
      if (Math.min(...foreground) <= 100) return 0;
      let overlap = 0;
      // Adjacent CSS grid cells can differ by one raster pixel after resizing
      // to 256px. Allow that translation, keeping scale and rotation unchanged.
      for (const dx of [-1, 0, 1]) {
        for (const dy of [-1, 0, 1]) {
          let intersection = 0;
          for (let y = 0; y < 256; y++) {
            for (let x = 0; x < 256; x++) {
              const targetX = x + dx,
                targetY = y + dy;
              if (
                targetX >= 0 &&
                targetX < 256 &&
                targetY >= 0 &&
                targetY < 256 &&
                masks[0][y * 256 + x] &&
                masks[1][targetY * 256 + targetX]
              )
                intersection++;
            }
          }
          overlap = Math.max(
            overlap,
            intersection / (foreground[0] + foreground[1] - intersection),
          );
        }
      }
      return overlap;
    },
    [[...first], [...second]],
  );
}
