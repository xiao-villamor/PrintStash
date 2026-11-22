import { devices } from "@playwright/test";

/** Shared desktop render for browser checks and visual review artifacts. */
export const DESKTOP_CHROMIUM = {
  ...devices["Desktop Chrome"],
  viewport: { width: 1920, height: 1080 },
  deviceScaleFactor: 1,
};
