import { DURATION } from "@/lib/overlay";

/** Navigation relationship used to select the folder entrance class. */
export type FolderMove = "forward" | "back" | "jump";

const isInside = (path: string, ancestor: string) =>
  ancestor === "" || path.startsWith(`${ancestor}/`);

/** Drilling into a descendant is `forward`, climbing to an ancestor is `back`,
 *  anything else (sibling, search, a deep link) is a `jump`. */
export function folderMove(from: string, to: string): FolderMove {
  if (from === to) throw new Error("folderMove needs two different paths");
  if (isInside(to, from)) return "forward";
  if (isInside(from, to)) return "back";
  return "jump";
}

/** Preserve reading-position geometry across every navigation relationship. */
export const FOLDER_MOVE_CLASS = {
  forward: "folder-in-forward",
  back: "folder-in-back",
  jump: "fade-in",
} satisfies Record<FolderMove, string>;

/** Card wrappers share the existing depth entrance on folder navigation. */
export const FOLDER_CARDS_CLASS = {
  forward: "folder-cards-forward",
  back: "folder-cards-back",
  jump: "folder-cards-jump",
} satisfies Record<FolderMove, string>;

/** Keep the entrance window aligned with the shared slow motion timing. */
export const FOLDER_ENTRANCE_MS = DURATION.slow;
