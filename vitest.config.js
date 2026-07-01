import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["opai/assets/web/__tests__/**/*.test.js"],
    environment: "node",
  },
});
