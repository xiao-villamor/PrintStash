/**
 * Names often arrive as file stems (`Shoe+Rack+-+Shelf`, `skadis_ruler`).
 * Remove fused STL/3MF extensions from separated stems, keeping any numeric suffix.
 * Show `+` and `_` runs as spaces without changing the stored name.
 */
export function displayModelName(name: string): string {
  const fusedExtension = /^(.*[-_+ ].*[a-z0-9])(?:stl|3mf)(\d*)$/i.exec(name);
  const base = fusedExtension ? `${fusedExtension[1]} ${fusedExtension[2]}` : name;
  const shown = base
    .replace(/[+_]+/g, " ")
    .replace(/\s{2,}/g, " ")
    .trim();
  return shown || name;
}
