/** Wire-compatible search values: strings stay strings; repetitions stay repeated parameters. */
export type AppSearch = Record<string, string | string[]>;

export function parseAppSearch(search: string): AppSearch {
  const params = new URLSearchParams(search);
  return Object.fromEntries(
    [...new Set(params.keys())].map((key) => {
      const values = params.getAll(key);
      return [key, values.length === 1 ? values[0]! : values];
    }),
  );
}

export function stringifyAppSearch(search: AppSearch): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(search)) {
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, item);
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}
