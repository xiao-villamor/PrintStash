/** Use the displayed artwork and title as native drag feedback. */
export function setModelDragImage(transfer: DataTransfer, card: HTMLElement, name: string) {
  if (!transfer.setDragImage) return;
  const canvas = card.ownerDocument.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context) return;

  const style = getComputedStyle(card);
  const image = card.querySelector<HTMLImageElement>("[data-model-preview] img");
  // Reuse decoded local artwork. A foreign image may prevent native drag capture.
  const hasThumbnail =
    image?.complete &&
    image.naturalWidth > 0 &&
    new URL(image.currentSrc, card.ownerDocument.baseURI).origin ===
      card.ownerDocument.location.origin;
  const padding = 12;
  const textX = hasThumbnail ? 50 : padding;
  const font = `600 13px ${style.fontFamily}`;
  context.font = font;
  canvas.width = Math.min(240, textX + Math.ceil(context.measureText(name).width) + padding);
  canvas.height = 48;

  context.beginPath();
  context.roundRect(0.5, 0.5, canvas.width - 1, canvas.height - 1, 6);
  context.fillStyle = style.getPropertyValue("--popover");
  context.fill();
  context.strokeStyle = style.getPropertyValue("--border-strong");
  context.stroke();

  if (hasThumbnail) {
    const size = 32;
    const offset = 8;
    const scale = Math.min(size / image.naturalWidth, size / image.naturalHeight);
    const width = image.naturalWidth * scale;
    const height = image.naturalHeight * scale;
    context.fillStyle = style.getPropertyValue("--plate");
    context.fillRect(offset, offset, size, size);
    context.drawImage(
      image,
      offset + (size - width) / 2,
      offset + (size - height) / 2,
      width,
      height,
    );
  }

  context.font = font;
  context.textBaseline = "middle";
  context.fillStyle = style.getPropertyValue("--popover-foreground");
  const textWidth = canvas.width - textX - padding;
  let title = name;
  if (context.measureText(title).width > textWidth) {
    const characters = Array.from(name);
    while (
      characters.length > 0 &&
      context.measureText(`${characters.join("")}…`).width > textWidth
    )
      characters.pop();
    title = `${characters.join("")}…`;
  }
  context.fillText(title, textX, canvas.height / 2);
  transfer.setDragImage(canvas, 12, 12);
}
