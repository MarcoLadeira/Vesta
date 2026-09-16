import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    include: ["vesta/assets/web/__tests__/**/*.test.js"],
    environment: "node",
  },
});
