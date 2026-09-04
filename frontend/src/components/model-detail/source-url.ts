/** Render-boundary defense for remotely captured provenance links. */
export function safeHttpUrl(value: string): string | null {
  if (/\p{C}/u.test(value)) return null;
  try {
    const url = new URL(value);
    if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password) {
      return null;
    }
    return url.href;
  } catch {
    return null;
  }
}
