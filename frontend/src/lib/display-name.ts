/**
 * Names often arrive as file stems (`Shoe+Rack+-+Shelf`, `skadis_ruler`).
 * Show `+` and `_` runs as spaces; the stored name and its tooltip stay exact.
 */
export function displayModelName(name: string): string {
  const shown = name
    .replace(/[+_]+/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
  return shown || name;
}
