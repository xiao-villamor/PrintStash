import type { SourceGalleryImage } from "./source-gallery.ts";

export function galleryPicker(images: SourceGalleryImage[] | null) {
  const element = document.createElement("fieldset");
  element.className = "publication-gallery";
  const legend = document.createElement("legend");
  element.append(legend);
  if (images === null) {
    legend.textContent = "Gallery unavailable";
    return { element, selectedImages: () => [] };
  }

  const grid = document.createElement("div");
  grid.className = "gallery-grid";
  const toggleAll = document.createElement("button");
  toggleAll.className = "text-button text-action";
  toggleAll.type = "button";
  const coverLabel = document.createElement("label");
  coverLabel.className = "gallery-cover";
  const cover = document.createElement("select");
  coverLabel.append("Gallery cover", cover);

  const options = images.map((image, index) => {
    const name = `Image ${index + 1}`;
    const option = document.createElement("label");
    option.className = "gallery-option";
    const thumbnail = document.createElement("img");
    thumbnail.alt = "";
    thumbnail.loading = "lazy";
    thumbnail.referrerPolicy = "no-referrer";
    thumbnail.src = image.url;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    const caption = document.createElement("span");
    caption.className = "gallery-caption";
    caption.append(input, name);
    option.append(thumbnail, caption);
    grid.append(option);
    return { image, name, input };
  });

  function update() {
    const previousCover = cover.value;
    const included = options.filter(({ input }) => input.checked);
    legend.textContent = `Images (${included.length} of ${options.length} selected)`;
    toggleAll.textContent = included.length === options.length ? "Deselect all" : "Select all";
    coverLabel.hidden = included.length === 0;
    cover.disabled = included.length < 2;
    cover.replaceChildren(...included.map(({ image, name }) => new Option(name, image.id)));
    if (included.some(({ image }) => image.id === previousCover)) cover.value = previousCover;
  }

  toggleAll.addEventListener("click", () => {
    const select = options.some(({ input }) => !input.checked);
    for (const { input } of options) input.checked = select;
    update();
  });
  grid.addEventListener("change", update);
  element.append(toggleAll, grid, coverLabel);
  update();

  return {
    element,
    selectedImages() {
      const included = options.filter(({ input }) => input.checked).map(({ image }) => image);
      const first = included.find((image) => image.id === cover.value);
      return first ? [first, ...included.filter((image) => image !== first)] : included;
    },
  };
}
