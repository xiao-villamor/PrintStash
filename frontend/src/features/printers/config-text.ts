export function textareaConfigText(raw: string) {
  return raw.replace(/\r\n?/g, "\n");
}

function rawOffset(raw: string, offset: number) {
  let index = 0;
  for (let count = 0; count < offset; count++, index++) {
    if (raw[index] === "\r" && raw[index + 1] === "\n") index++;
  }
  return index;
}

/** The HTML textarea exposes LF. Keep the original separators around each edit. */
export function applyConfigTextEdit(raw: string, next: string) {
  const before = textareaConfigText(raw);
  if (before === next) return raw;
  let start = 0;
  const overlap = Math.min(before.length, next.length);
  while (start < overlap && before[start] === next[start]) start++;

  let oldEnd = before.length;
  let newEnd = next.length;
  while (oldEnd > start && newEnd > start && before[oldEnd - 1] === next[newEnd - 1]) {
    oldEnd--;
    newEnd--;
  }

  const rawStart = rawOffset(raw, start);
  const rawEnd = rawOffset(raw, oldEnd);
  const existingBreaks = raw.slice(rawStart, rawEnd).match(/\r\n|\r|\n/g) ?? [];
  const defaultBreak = raw.match(/\r\n|\r|\n/)?.[0] ?? "\n";
  let line = 0;
  const replacement = next
    .slice(start, newEnd)
    .replace(/\n/g, () => existingBreaks[line++] ?? defaultBreak);
  return raw.slice(0, rawStart) + replacement + raw.slice(rawEnd);
}
