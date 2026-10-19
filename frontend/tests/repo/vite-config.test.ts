/** Application dependency discovery must not scan standalone experimental build roots. */
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { resolveConfig } from "vite";

const FRONTEND_ROOT = resolve(__dirname, "../..");
async function applicationConfig() {
  return resolveConfig(
    { root: FRONTEND_ROOT, configFile: resolve(FRONTEND_ROOT, "vite.config.ts") },
    "serve",
  );
}
describe("application dependency discovery", () => {
  it("scans only the application HTML entry", async () => {
    const config = await applicationConfig();

    expect(config.optimizeDeps.entries).toEqual(["index.html"]);
  });
  it("prebundles every lazy mesh dependency", async () => {
    const config = await applicationConfig();

    expect(config.optimizeDeps.include).toEqual(
      expect.arrayContaining([
        "three/webgpu",
        "three/addons/loaders/STLLoader.js",
        "@react-three/fiber",
        "@react-three/drei",
      ]),
    );
  });
});
