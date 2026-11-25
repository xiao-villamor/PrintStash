// The 3MF viewer derivative stores a uniform RGB triplet in its binary STL
// header. Ordinary STL files do not carry this marker.
const prefix = new TextEncoder().encode("PrintStash RGB:");

export function embeddedStlColor(buffer: ArrayBuffer): string | undefined {
  if (buffer.byteLength < 84) return;
  const header = new Uint8Array(buffer, 0, 84);
  const facets = new DataView(buffer).getUint32(80, true);
  if (buffer.byteLength !== 84 + facets * 50) return;
  if (!prefix.every((byte, index) => header[index] === byte)) return;
  const hex = Array.from(header.subarray(prefix.length, prefix.length + 3), (byte) =>
    byte.toString(16).padStart(2, "0"),
  );
  return `#${hex.join("")}`;
}
