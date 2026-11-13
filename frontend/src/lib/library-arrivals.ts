/** Keys of the entries a Library view shows (`m:`/`p:`/`c:` for a Model, a
 *  multipart set or a folder), so one id of each kind cannot collide. */
export function entryKeys(
  folders: readonly { id: number }[],
  items: readonly { kind: "model" | "multipart"; value: { id: number } }[],
): string[] {
  return Array.from(
    new Set([
      ...folders.map((folder) => `c:${folder.id}`),
      ...items.map((item) => `${item.kind === "multipart" ? "p" : "m"}:${item.value.id}`),
    ]),
  );
}

/** What turned up since the previous reading of the same view. */
export function arrivingKeys(
  previous: ReadonlySet<string>,
  current: readonly string[],
): Set<string> {
  return new Set(current.filter((key) => !previous.has(key)));
}
