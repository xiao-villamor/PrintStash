import { createContext, useContext } from "react";

export type StartupPart = "cards" | "tree";
export type StartupOutcome = "ready" | "failed";
export type SecondaryRead = "filters" | "saved-views" | "search" | "inbox" | "activity";

export interface LibraryStartup {
  canLoad: (read: SecondaryRead) => boolean;
  request: (read: SecondaryRead) => void;
  settle: (part: StartupPart, outcome: StartupOutcome) => void;
}

// Components outside the library keep their ordinary fetching behavior.
export const LibraryStartupContext = createContext<LibraryStartup>({
  canLoad: () => true,
  request: () => {},
  settle: () => {},
});

export function useLibraryStartup(): LibraryStartup {
  return useContext(LibraryStartupContext);
}
