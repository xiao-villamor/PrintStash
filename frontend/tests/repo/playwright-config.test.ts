/** Every browser entry point renders desktop reviews at the agreed 1080p size. */
import { readdirSync } from "node:fs";
import { resolve } from "node:path";
import type { PlaywrightTestConfig } from "@playwright/test";
import { loadConfigFromFile } from "vite";
import { describe, expect, it } from "vitest";

const root = resolve(import.meta.dirname, "../..");
const configs = readdirSync(root).filter((name) => /^playwright(?:\..+)?\.config\.ts$/.test(name));

describe("Playwright desktop defaults", () => {
  it.each(configs)("renders the 1080p desktop in %s", async (name) => {
    const environment = { ...process.env };
    try {
      // Load TypeScript as a Node config, preserving import.meta.url. Importing
      // through jsdom's module graph rewrites relative URLs as browser assets.
      const loaded = await loadConfigFromFile(
        { command: "serve", mode: "test" },
        resolve(root, name),
      );
      expect(loaded).not.toBeNull();
      // SAFETY: these local modules export Playwright defineConfig objects,
      // checked by tsc; the generic Node config loader gives them Vite's type.
      const config = loaded!.config as PlaywrightTestConfig;
      const projects = config.projects ?? [];
      const devices = projects.map((project) => ({ ...config.use, ...project.use }));

      expect(projects.length).toBeGreaterThan(0);
      expect(
        devices.map(({ viewport, deviceScaleFactor, isMobile }) => ({
          viewport,
          deviceScaleFactor,
          isMobile,
        })),
      ).toEqual(
        projects.map(() => ({
          viewport: { width: 1920, height: 1080 },
          deviceScaleFactor: 1,
          isMobile: false,
        })),
      );
    } finally {
      process.env = environment;
    }
  });
});
