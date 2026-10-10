import { createContext, useContext } from "react";

export type StartupPart = "cards" | "tree" | "media";
export type StartupOutcome = "idle" | "pending" | "ready" | "failed";
export type SecondaryRead = "filters" | "saved-views" | "search" | "inbox" | "activity";

export interface LibraryStartup {
  complete: boolean;
  canLoad: (read: SecondaryRead) => boolean;
  request: (read: SecondaryRead) => void;
  settle: (part: StartupPart, outcome: StartupOutcome) => void;
}

// Components outside the library keep their ordinary fetching behavior.
export const LibraryStartupContext = createContext<LibraryStartup>({
  complete: true,
  canLoad: () => true,
  request: () => {},
  settle: () => {},
});

export function useLibraryStartup(): LibraryStartup {
  return useContext(LibraryStartupContext);
}
