import { defineConfig } from "vitest/config";
import { WxtVitest } from "wxt/testing/vitest-plugin";

export default defineConfig({
  plugins: [WxtVitest()],
  test: {
    environment: "node",
    include: ["tests/ci/real-backend-capture.test.ts"],
    restoreMocks: true,
  },
});
